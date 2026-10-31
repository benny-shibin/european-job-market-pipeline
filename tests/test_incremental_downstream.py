"""Incremental transformation, crash recovery, scope and multi-source contracts."""
import json
from pathlib import Path
import shutil
import sqlite3
from unittest.mock import MagicMock

import duckdb
import polars as pl
import pytest
import requests

from src.job_market.ingestion import incremental as ingestion
from src.job_market.ingestion.sources import parse_payload, validate_job
from src.job_market.transformations import bronze_2_silver as silver
from src.job_market.transformations import extract_skills as skills
from src.job_market.transformations import silver_2_gold as gold
from src.job_market.analytics import daily_snapshots as snapshots
from src.job_market.quality.validators import validate_silver

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    shutil.copytree(ROOT / 'config', tmp_path / 'config')
    return tmp_path


def job(slug='one', **changes):
    return dict(slug=slug, title='Python Engineer', company_name='Acme', location='Europe',
                remote=True, url=f'https://example.com/{slug}', description='Python SQL',
                created_at=1793404800, tags=[]) | changes


def responses(monkeypatch, *payloads):
    session = MagicMock()
    session.__enter__.return_value = session
    values = []
    for payload in payloads:
        if isinstance(payload, Exception):
            values.append(payload)
        else:
            response = MagicMock()
            response.json.return_value = payload
            values.append(response)
    session.get.side_effect = values
    monkeypatch.setattr(ingestion.requests, 'Session', lambda: session)
    monkeypatch.setattr(ingestion.time, 'sleep', lambda seconds: None)
    return session


def fetch(monkeypatch, jobs, next_page=None, **kwargs):
    responses(monkeypatch, {'data': jobs, 'links': {'next': next_page}})
    return ingestion.run_incremental(**kwargs)


def build():
    silver_stats = silver.build_silver()
    validate_silver()
    skill_stats = skills.build_job_skills()
    gold_stats = gold.build_gold()
    return silver_stats, skill_stats, gold_stats


def test_noop_and_changed_jobs(workspace, monkeypatch):
    fetch(monkeypatch, [job(), job('two', title='SQL Analyst')])
    first = build()
    assert first[0]['changed_jobs'] == first[1] == first[2]['changed_jobs'] == 2
    paths = list(Path('data/silver').glob('*/*.parquet'))
    before = {str(path): (path.stat().st_mtime_ns, path.read_bytes()) for path in paths}
    with duckdb.connect(str(gold.GOLD_PATH), read_only=True) as con:
        assert con.execute("SELECT COUNT(*) FROM duckdb_views() WHERE view_name LIKE 'incoming_%' AND NOT temporary").fetchone()[0] == 0
        old = con.execute("SELECT * FROM fact_job_postings WHERE job_id='two'").fetchall()
        key = con.execute("SELECT company_key FROM dim_companies WHERE company_name='Acme'").fetchone()[0]
    assert fetch(monkeypatch, [job(), job('two', title='SQL Analyst')])['unchanged'] == 2
    result = build()
    assert result[0]['changed_jobs'] == result[1] == result[2]['changed_jobs'] == 0
    assert before == {str(path): (path.stat().st_mtime_ns, path.read_bytes()) for path in paths}
    assert silver.build_silver() == dict(pages_read=0, changed_jobs=0)
    fetch(monkeypatch, [job(description='Docker', company_name='New company'), job('two', title='SQL Analyst')])
    changed = build()
    assert changed[0]['changed_jobs'] == changed[1] == changed[2]['changed_jobs'] == 1
    with duckdb.connect(str(gold.GOLD_PATH), read_only=True) as con:
        assert con.execute("SELECT * FROM fact_job_postings WHERE job_id='two'").fetchall() == old
        assert con.execute("SELECT company_key FROM dim_companies WHERE company_name='Acme'").fetchone()[0] == key
        assert con.execute("SELECT s.skill_name FROM bridge_job_skills b JOIN dim_skills s USING(skill_key) WHERE job_id='one' ORDER BY s.skill_name").fetchall() == [('Docker',), ('Python',)]
    manifest = json.loads(sorted(Path('data/bronze').rglob('manifest.json'), key=lambda p: p.stat().st_mtime_ns)[-1].read_text())
    assert manifest['status'] == 'complete' and manifest['pagination_complete'] == 1
    assert manifest['counts']['updated'] == 1
    assert manifest['changes'][0]['version'] == 2
    assert build()[2]['changed_jobs'] == 0


def test_bronze_state_crash_replays_once(workspace, monkeypatch):
    original = ingestion._apply_page
    monkeypatch.setattr(ingestion, '_apply_page', lambda *args: (_ for _ in ()).throw(RuntimeError('crash before state')))
    with pytest.raises(RuntimeError, match='crash before state'):
        fetch(monkeypatch, [job()])
    assert list(Path('data/bronze').rglob('page_1.json'))
    monkeypatch.setattr(ingestion, '_apply_page', original)
    with sqlite3.connect(ingestion.DB_PATH) as con:
        assert con.execute('SELECT COUNT(*) FROM job_history').fetchone()[0] == 0
        ingestion.recover_pages(con, ingestion.BRONZE_PATH.parent)
        ingestion.recover_pages(con, ingestion.BRONZE_PATH.parent)
        assert con.execute('SELECT version FROM job_state').fetchall() == [(1,)]
        assert con.execute('SELECT COUNT(*) FROM job_history').fetchone()[0] == 1
        assert json.loads(con.execute('SELECT counts_json FROM ingestion_runs').fetchone()[0])['new'] == 1
    manifest = json.loads(next(Path('data/bronze').rglob('manifest.json')).read_text())
    assert manifest['changes'][0]['job_id'] == 'one'
    assert manifest['fetched_pages'] == [1]
    build()
    fetch(monkeypatch, [job()])
    assert build()[0]['changed_jobs'] == 0


def test_export_failure_recovers_without_reparsing(workspace, monkeypatch):
    fetch(monkeypatch, [job()])
    original = silver.atomic_parquet
    monkeypatch.setattr(silver, 'atomic_parquet', lambda *args: (_ for _ in ()).throw(OSError('disk failure')))
    with pytest.raises(OSError):
        silver.build_silver()
    monkeypatch.setattr(silver, 'atomic_parquet', original)
    assert silver.build_silver() == dict(pages_read=0, changed_jobs=0)
    assert pl.read_parquet(silver.SILVER_DIR / 'jobs.parquet').height == 1
    skills.build_job_skills()
    gold.build_gold()


def test_partial_and_failed_fetches_do_not_imply_absence(workspace, monkeypatch):
    fetch(monkeypatch, [job('one'), job('two')])
    fetch(monkeypatch, [job('one')], next_page='page2', max_pages=1)
    with sqlite3.connect(ingestion.DB_PATH) as con:
        assert con.execute("SELECT lifecycle_state FROM job_state WHERE job_id='two'").fetchone()[0] == 'observed'
    responses(monkeypatch, {'data': [job('one')], 'links': {'next': 'page2'}}, requests.Timeout('offline'))
    with pytest.raises(requests.Timeout):
        ingestion.run_incremental(max_pages=2)
    with sqlite3.connect(ingestion.DB_PATH) as con:
        assert con.execute("SELECT lifecycle_state FROM job_state WHERE job_id='two'").fetchone()[0] == 'observed'
    fetch(monkeypatch, [job('one')])
    with sqlite3.connect(ingestion.DB_PATH) as con:
        assert con.execute("SELECT lifecycle_state FROM job_state WHERE job_id='two'").fetchone()[0] == 'not_observed'
        assert not con.execute("SELECT 1 FROM job_state WHERE lifecycle_state='confirmed_closed'").fetchone()
    fetch(monkeypatch, [job('one'), job('two')])
    with sqlite3.connect(ingestion.DB_PATH) as con:
        assert con.execute("SELECT version, lifecycle_state FROM job_state WHERE job_id='two'").fetchone() == (1, 'observed')
        assert con.execute("SELECT COUNT(*) FROM lifecycle_events WHERE job_id='two' AND state='observed'").fetchone()[0] == 2


def test_volatile_changes_do_not_increment_versions(workspace, monkeypatch):
    fetch(monkeypatch, [job(views=1)])
    assert fetch(monkeypatch, [job(views=2)])['unchanged'] == 1


def test_multi_source_identity_attribution_and_models(workspace, monkeypatch):
    fetch(monkeypatch, [job('42')])
    responses(monkeypatch, {'jobs': [dict(id=42, jobTitle='Python Engineer', companyName='Acme', jobGeo='Europe',
               jobDescription='Python SQL', url='https://jobicy.com/jobs/42', pubDate='2026-10-31T00:00:00Z', jobIndustry=[])],
               'nextCursor': None, 'hasMore': False})
    ingestion.run_incremental(source='jobicy')
    build()
    with duckdb.connect(str(gold.GOLD_PATH), read_only=True) as con:
        assert con.execute("SELECT source, job_id FROM fact_job_postings ORDER BY source").fetchall() == [('arbeitnow', '42'), ('jobicy', '42')]
        assert con.execute('SELECT COUNT(*) FROM duplicate_candidates').fetchone()[0] == 1
        assert con.execute('SELECT SUM(job_count) FROM skill_cooccurrence').fetchone()[0] == 2
        assert con.execute('SELECT SUM(job_count) FROM posting_date_summary').fetchone()[0] == 2
        for view in ['skill_demand', 'remote_skill_summary', 'company_summary', 'geography_summary']:
            assert con.execute(f'SELECT * FROM {view}').fetchall()
    snapshots.create_daily_snapshot('2026-10-31')
    snapshots.create_daily_snapshot('2026-10-31')
    with sqlite3.connect(snapshots.SNAPSHOT_DB) as con:
        assert con.execute('SELECT total_jobs FROM daily_market_snapshots').fetchall() == [(2,)]
        assert con.execute('SELECT COUNT(*) FROM daily_source_market_snapshots').fetchone()[0] == 2
        assert con.execute("SELECT job_count FROM daily_skill_snapshots WHERE skill_name='Python'").fetchone()[0] == 2
    with pytest.raises(RuntimeError, match='once per hour'):
        ingestion.run_incremental(source='jobicy')


def test_gold_failure_rolls_back(workspace, monkeypatch):
    fetch(monkeypatch, [job()])
    build()
    fetch(monkeypatch, [job(title='New title')])
    silver.build_silver()
    skills.build_job_skills()
    monkeypatch.setattr(gold, 'publish_report', lambda *args, **kwargs: (_ for _ in ()).throw(ValueError('bad gold')))
    with pytest.raises(ValueError, match='bad gold'):
        gold.build_gold()
    with duckdb.connect(str(gold.GOLD_PATH), read_only=True) as con:
        assert con.execute('SELECT title FROM fact_job_postings').fetchone()[0] == 'Python Engineer'


@pytest.mark.parametrize('payload', [[], {}, {'data': {}}, {'data': [42]}, {'data': [], 'links': []}])
def test_api_schema_rejected(payload):
    with pytest.raises(ValueError):
        parse_payload('arbeitnow', payload)


@pytest.mark.parametrize('changes', [dict(remote='yes'), dict(created_at='yesterday'), dict(tags='Python'), dict(title=42)])
def test_invalid_api_fields(changes):
    with pytest.raises(ValueError):
        validate_job(job(**changes))


def test_quality_report_identifies_bad_data(workspace, monkeypatch):
    fetch(monkeypatch, [job(title='')])
    silver.build_silver()
    with pytest.raises(ValueError, match='title_not_blank'):
        validate_silver()
    report = json.loads(next(Path('data/state/quality').rglob('silver.json')).read_text())
    assert report['passed'] is False
    assert report['checks']['arbeitnow.title_not_blank'] is False


def test_page_state_transaction_rolls_back_and_restarts(workspace, monkeypatch):
    original = ingestion.classify_job
    count = 0

    def fail_second(*args, **kwargs):
        nonlocal count
        count += 1
        if count == 2:
            raise RuntimeError('interrupted state transaction')
        return original(*args, **kwargs)

    monkeypatch.setattr(ingestion, 'classify_job', fail_second)
    with pytest.raises(RuntimeError):
        fetch(monkeypatch, [job('one'), job('two')])
    with sqlite3.connect(ingestion.DB_PATH) as con:
        assert con.execute('SELECT COUNT(*) FROM job_state').fetchone()[0] == 0
    monkeypatch.setattr(ingestion, 'classify_job', original)
    with sqlite3.connect(ingestion.DB_PATH) as con:
        ingestion.recover_pages(con, ingestion.BRONZE_PATH.parent)
        assert con.execute('SELECT COUNT(*) FROM job_history').fetchone()[0] == 2
        assert con.execute('SELECT COUNT(*) FROM ingestion_pages').fetchone()[0] == 1


def test_missing_pagination_does_not_mark_absence(workspace, monkeypatch):
    fetch(monkeypatch, [job('one'), job('two')])
    responses(monkeypatch, {'data': [job('one')]})
    with pytest.raises(ValueError, match='pagination metadata'):
        ingestion.run_incremental()
    with sqlite3.connect(ingestion.DB_PATH) as con:
        assert con.execute("SELECT lifecycle_state FROM job_state WHERE job_id='two'").fetchone()[0] == 'observed'


def test_retry_policy_is_bounded(workspace, monkeypatch):
    session = responses(monkeypatch, {'data': [job()], 'links': {'next': None}})
    ingestion.run_incremental()
    adapter = session.mount.call_args.args[1]
    assert adapter.max_retries.total == 3
    assert adapter.max_retries.respect_retry_after_header is True
    assert adapter.max_retries.allowed_methods == ['GET']


def test_skill_export_failure_and_dictionary_change(workspace, monkeypatch):
    fetch(monkeypatch, [job()])
    silver.build_silver()
    original = skills.atomic_parquet
    monkeypatch.setattr(skills, 'atomic_parquet', lambda *args: (_ for _ in ()).throw(OSError('disk full')))
    with pytest.raises(OSError):
        skills.build_job_skills()
    monkeypatch.setattr(skills, 'atomic_parquet', original)
    assert skills.build_job_skills() == 0
    assert Path('data/silver/arbeitnow/job_skills.parquet').exists()
    dictionary = Path('config/skills.yaml')
    dictionary.write_text('skills:\n  CustomSkill: [Python]\n')
    assert skills.build_job_skills() == 1
    assert pl.read_parquet('data/silver/arbeitnow/job_skills.parquet')['skill_name'].to_list() == ['CustomSkill']


def test_snapshot_failure_preserves_previous_date_counts(workspace, monkeypatch):
    fetch(monkeypatch, [job()])
    build()
    snapshots.create_daily_snapshot('2026-10-31')
    fetch(monkeypatch, [job(), job('two')])
    build()
    monkeypatch.setattr(snapshots, 'publish_report', lambda *args, **kwargs: (_ for _ in ()).throw(ValueError('snapshot consistency')))
    with pytest.raises(ValueError, match='snapshot consistency'):
        snapshots.create_daily_snapshot('2026-10-31')
    with sqlite3.connect(snapshots.SNAPSHOT_DB) as con:
        assert con.execute('SELECT total_jobs FROM daily_market_snapshots').fetchone()[0] == 1
        assert con.execute("SELECT job_count FROM daily_skill_snapshots WHERE skill_name='Python'").fetchone()[0] == 1


def test_jobicy_cursor_and_timestamp_normalization(workspace, monkeypatch):
    payload = dict(jobs=[dict(id=42, jobTitle='Python &amp; SQL', companyName='Acme', jobGeo='Europe',
                jobDescription='Python', url='https://jobicy.com/jobs/42', pubDate='2026-10-31T02:00:00+02:00')],
                nextCursor='opaque', hasMore=True)
    session = responses(monkeypatch, payload, dict(jobs=[], nextCursor=None, hasMore=False))
    ingestion.run_incremental(source='jobicy')
    assert session.get.call_args_list[1].kwargs['params'] == dict(count=100, geo='europe', cursor='opaque')
    normalized, cursor = parse_payload('jobicy', payload)
    assert normalized[0]['title'] == 'Python & SQL'
    from datetime import datetime, timezone
    assert datetime.fromtimestamp(normalized[0]['created_at'], timezone.utc).isoformat() == '2026-10-31T00:00:00+00:00'
    with sqlite3.connect(ingestion.DB_PATH) as con:
        assert con.execute('SELECT pagination_complete FROM ingestion_runs').fetchone()[0] == 1


def test_gold_rejects_orphan_skill_inputs(workspace, monkeypatch):
    fetch(monkeypatch, [job()])
    build()
    path = Path('data/silver/arbeitnow/job_skills.parquet')
    frame = pl.read_parquet(path)
    pl.concat([frame, pl.DataFrame(dict(source=['arbeitnow'], job_id=['missing'], skill_name=['Python']))]).write_parquet(path)
    with pytest.raises(ValueError, match='incoming_skill_jobs_exist'):
        gold.build_gold()
    with duckdb.connect(str(gold.GOLD_PATH), read_only=True) as con:
        assert con.execute('SELECT COUNT(*) FROM fact_job_postings').fetchone()[0] == 1
        assert all(gold.check_integrity(con).values())


def test_timezone_is_required_for_bronze(workspace):
    path = Path('data/bronze/arbeitnow/date=2026-10-31/run=bad/page_1.json')
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(dict(source='arbeitnow', run_id='bad', page=1,
                                   ingested_at='2026-10-31T10:00:00', payload=dict(data=[job()]))))
    with pytest.raises(ValueError, match='timezone'):
        silver.build_silver()


def test_malformed_pagination_and_publication_time_are_rejected():
    with pytest.raises(ValueError, match='next-page'):
        parse_payload('arbeitnow', dict(data=[], links=dict(next=False)))
    with pytest.raises(ValueError, match='pagination flags'):
        parse_payload('jobicy', dict(jobs=[], hasMore=False, nextCursor='token'))
    with pytest.raises(ValueError):
        parse_payload('jobicy', dict(jobs=[dict(id=1, pubDate='not-a-date')], nextCursor=None, hasMore=False))


def test_gold_dates_use_utc_at_local_midnight(workspace):
    from datetime import datetime
    path = Path('data/bronze/arbeitnow/date=2026-10-30/run=utc/page_1.json')
    path.parent.mkdir(parents=True)
    created_at = int(datetime.fromisoformat('2026-10-30T23:30:00+00:00').timestamp())
    path.write_text(json.dumps(dict(source='arbeitnow', run_id='utc', page=1,
                                   ingested_at='2026-10-30T23:30:00+00:00', payload=dict(data=[job(created_at=created_at)]))))
    build()
    with duckdb.connect(str(gold.GOLD_PATH), read_only=True) as con:
        con.execute("SET TimeZone='Europe/Berlin'")
        assert con.execute('SELECT date_key FROM fact_job_postings').fetchone()[0] == 20261030
        assert con.execute('SELECT CAST(posting_date AS VARCHAR) FROM posting_date_summary').fetchone()[0] == '2026-10-30'


def test_source_health_verifies_lineage_and_reports_corruption(workspace, monkeypatch):
    from src.job_market.quality.source_health import audit_sources
    fetch(monkeypatch, [job()])
    build()
    report = {row['source']: row for row in audit_sources()['sources']}
    assert report['arbeitnow']['status'] == 'verified'
    assert all(report['arbeitnow']['checks'].values())
    assert report['linkedin']['status'] == report['stepstone']['status'] == 'access_required'
    assert report['jobicy']['status'] == 'not_ingested'
    with duckdb.connect(str(gold.GOLD_PATH)) as con:
        con.execute("UPDATE fact_job_postings SET title='Corrupted title'")
    report = {row['source']: row for row in audit_sources()['sources']}
    assert report['arbeitnow']['status'] == 'needs_attention'
    assert report['arbeitnow']['checks']['silver_gold_content_matches'] is False
