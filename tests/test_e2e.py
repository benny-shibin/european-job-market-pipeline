"""Offline integration tests: real files, CLI transforms, SQL and dashboard."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import duckdb
import polars as pl
import pytest
import requests
from streamlit.testing.v1 import AppTest

from src.job_market.ingestion import arbeitnow

ROOT = Path(__file__).resolve().parents[1]


def job(slug, **overrides):
    record = dict(slug=slug, title='Python Engineer', company_name='Acme',
                  location='Berlin', remote=True, url=f'https://example.com/{slug}',
                  description='Python, SQL and AWS. Python again.',
                  created_at=1700000000, tags=['Docker'])
    return record | overrides


def run_module(workspace, module, check=True):
    env = os.environ | {'PYTHONPATH': str(ROOT)}
    result = subprocess.run([sys.executable, '-m', f'src.job_market.{module}'],
                          cwd=workspace, env=env, capture_output=True, text=True,
                          timeout=45, check=False)
    if check:
        assert result.returncode == 0, f'{module} failed:\n{result.stdout}\n{result.stderr}'
    return result


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    shutil.copytree(ROOT / 'config', tmp_path / 'config')
    shutil.copytree(ROOT / 'dashboard', tmp_path / 'dashboard',
                    ignore=shutil.ignore_patterns('__pycache__'))
    monkeypatch.setattr(arbeitnow, 'BRONZE_DIR', Path('data/bronze/arbeitnow'))
    return tmp_path


def ingest(monkeypatch, pages):
    responses = []
    for payload in pages:
        response = MagicMock()
        response.json.return_value = payload
        responses.append(response)
    session = MagicMock()
    session.__enter__.return_value = session
    session.get.side_effect = responses
    monkeypatch.setattr(arbeitnow.requests, 'Session', lambda: session)
    monkeypatch.setattr(arbeitnow, 'time', SimpleNamespace(sleep=lambda _: None))
    count = arbeitnow.ingest_jobs(max_pages=5)
    return count, session


def build(workspace):
    for module in ['transformations.bronze_2_silver', 'quality.validators',
                   'transformations.extract_skills', 'transformations.silver_2_gold']:
        run_module(workspace, module)


def test_pipeline_and_dashboard(workspace, monkeypatch):
    count, session = ingest(monkeypatch, [
        {'data': [job('python'), job('analyst', title='SQL Analyst', remote=False,
                                   company_name='Beta', description='SQL', tags=[])],
         'links': {'next': 'page2'}},
        {'data': [job('unknown', title='Generalist', remote=None, location=None,
                      company_name='Acme', description='', tags=[])], 'links': {'next': None}},
    ])
    assert count == 3 and session.get.call_count == 2
    assert session.get.call_args_list[1].kwargs['params'] == {'page': 2}
    raw = list((workspace / 'data/bronze').rglob('page_*.json'))
    assert len(raw) == 2
    assert json.loads(raw[0].read_text())['source'] == 'arbeitnow'

    # A newer observation must replace the old version of the same source/job.
    newer = workspace / 'data/bronze/arbeitnow/date=2099-01-01/run=new/page_1.json'
    newer.parent.mkdir(parents=True)
    newer.write_text(json.dumps(dict(source='arbeitnow', run_id='new', page=1,
        ingested_at='2099-01-01T00:00:00+00:00',
        payload={'data': [job('python', title='Senior Python Engineer')]})))
    build(workspace)
    silver = pl.read_parquet(workspace / 'data/silver/arbeitnow/jobs.parquet')
    assert silver.height == 3
    assert silver.filter(pl.col('job_id') == 'python')['title'].item() == 'Senior Python Engineer'
    with duckdb.connect(str(workspace / 'data/gold/job_market.duckdb'), read_only=True) as con:
        assert con.execute('SELECT COUNT(*) FROM fact_job_postings').fetchone()[0] == 3
        assert con.execute('SELECT COUNT(*) FROM bridge_job_skills').fetchone()[0] == 5
        assert con.execute('SELECT COUNT(*) FROM fact_job_postings WHERE location_key IS NULL').fetchone()[0] == 1
        before = con.execute('SELECT * FROM fact_job_postings ORDER BY job_id').fetchall()
        for query in (ROOT / 'sql/analytics.sql').read_text().split(';'):
            if query.strip():
                assert con.execute(query).fetchall()
    build(workspace)
    with duckdb.connect(str(workspace / 'data/gold/job_market.duckdb'), read_only=True) as con:
        assert con.execute('SELECT * FROM fact_job_postings ORDER BY job_id').fetchall() == before

    app = AppTest.from_file(str(workspace / 'dashboard/app.py')).run(timeout=30)
    assert not app.exception
    assert app.metric[0].value == '3'
    app.text_input(key='search').set_value('Senior').run()
    assert not app.exception and app.metric[0].value == '1'
    app.button[0].click().run()
    app.multiselect(key='skills').select('SQL').run()
    assert not app.exception and app.metric[0].value == '2'
    app.selectbox(key='arrangement').select('Remote').run()
    assert not app.exception and app.metric[0].value == '1'
    app.text_input(key='search').set_value('no_such_job').run()
    assert not app.exception and app.metric[0].value == '0'
    assert app.info
    app.button[0].click().run()
    assert not app.exception and app.metric[0].value == '3'
    assert len(app.dataframe[0].value) == 3


def test_no_skill_mentions(workspace, monkeypatch):
    ingest(monkeypatch, [{'data': [job('general', title='Generalist', description='', tags=[])],
                          'links': {'next': None}}])
    build(workspace)
    with duckdb.connect(str(workspace / 'data/gold/job_market.duckdb'), read_only=True) as con:
        assert con.execute('SELECT COUNT(*) FROM dim_skills').fetchone()[0] == 0
        assert con.execute('SELECT COUNT(*) FROM fact_job_postings').fetchone()[0] == 1
    app = AppTest.from_file(str(workspace / 'dashboard/app.py')).run(timeout=30)
    assert not app.exception and app.metric[0].value == '1'


def test_validation_rejects_bad_silver(workspace):
    path = workspace / 'data/silver/arbeitnow/jobs.parquet'
    path.parent.mkdir(parents=True)
    pl.DataFrame(dict(source=['arbeitnow'] * 2, job_id=['duplicate'] * 2,
                      title=['Role', None], company=['Acme'] * 2,
                      ingested_at=['2026-01-01'] * 2)).write_parquet(path)
    result = run_module(workspace, 'quality.validators', check=False)
    assert result.returncode != 0
    assert 'unique_job_ids: FAIL' in result.stdout
    assert 'title_not_null: FAIL' in result.stdout


def test_ingestion_rejects_invalid_schema(workspace, monkeypatch):
    with pytest.raises(ValueError, match='Unexpected API response schema'):
        ingest(monkeypatch, [{'data': {'unexpected': 'object'}}])
    assert not list((workspace / 'data/bronze').rglob('page_*.json'))


def test_ingestion_propagates_http_failure(workspace, monkeypatch):
    session = MagicMock()
    session.__enter__.return_value = session
    session.get.return_value.raise_for_status.side_effect = requests.HTTPError('503')
    monkeypatch.setattr(arbeitnow.requests, 'Session', lambda: session)
    with pytest.raises(requests.HTTPError):
        arbeitnow.ingest_jobs()
    assert not list((workspace / 'data/bronze').rglob('page_*.json'))


def test_incremental_ingestion_state(workspace, monkeypatch):
    import sqlite3
    from src.job_market.ingestion import incremental

    session = MagicMock()
    session.__enter__.return_value = session
    monkeypatch.setattr(incremental.requests, 'Session', lambda: session)
    monkeypatch.setattr(incremental, 'DB_PATH', workspace / 'data/state/jobs.sqlite')
    monkeypatch.setattr(incremental, 'BRONZE_PATH', workspace / 'data/bronze/arbeitnow')
    for record, expected in [(job('tracked'), 'new'), (job('tracked'), 'unchanged'),
                             (job('tracked', title='Updated Python Engineer'), 'updated')]:
        session.get.return_value.json.return_value = {'data': [record], 'links': {'next': None}}
        stats = incremental.run_incremental()
        assert stats[expected] == 1 and sum(stats.values()) == 1
    with sqlite3.connect(incremental.DB_PATH) as con:
        assert con.execute('SELECT version FROM job_state').fetchone()[0] == 2
    assert len(list((workspace / 'data/bronze').rglob('page_*.json'))) == 3
    build(workspace)
    silver = pl.read_parquet(workspace / 'data/silver/arbeitnow/jobs.parquet')
    assert silver.height == 1 and silver['title'].item() == 'Updated Python Engineer'
