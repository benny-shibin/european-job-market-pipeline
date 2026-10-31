"""Historical dashboard integration checks against isolated local databases."""
import json
from pathlib import Path
import shutil
import sqlite3

import duckdb
import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def dashboard(tmp_path):
    shutil.copytree(ROOT / 'dashboard', tmp_path / 'dashboard',
                    ignore=shutil.ignore_patterns('__pycache__'))
    gold = tmp_path / 'data/gold/job_market.duckdb'
    gold.parent.mkdir(parents=True)
    with duckdb.connect(str(gold)) as con:
        con.execute("CREATE TABLE fact_job_postings AS SELECT 'arbeitnow' AS source, 'one' AS job_id, 'Engineer' AS title, 1 AS company_key, 1 AS location_key, TRUE AS remote, 'https://example.com' AS url, '2026-10-31' AS ingested_at")
        con.execute("CREATE TABLE dim_companies AS SELECT 1 AS company_key, 'Acme' AS company_name")
        con.execute("CREATE TABLE dim_locations AS SELECT 1 AS location_key, 'Berlin' AS location_name")
        con.execute("CREATE TABLE dim_skills AS SELECT 1 AS skill_key, 'Python' AS skill_name")
        con.execute("CREATE TABLE bridge_job_skills AS SELECT 'arbeitnow' AS source, 'one' AS job_id, 1 AS skill_key")
    return tmp_path


def snapshots(dashboard, market, skills=(), skill_table=True):
    path = dashboard / 'data/state/snapshots.sqlite'
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as con:
        con.execute('CREATE TABLE daily_market_snapshots (snapshot_date TEXT, total_jobs INTEGER, remote_jobs INTEGER)')
        con.executemany('INSERT INTO daily_market_snapshots VALUES (?, ?, ?)', market)
        if skill_table:
            con.execute('CREATE TABLE daily_skill_snapshots (snapshot_date TEXT, skill_name TEXT, job_count INTEGER)')
            con.executemany('INSERT INTO daily_skill_snapshots VALUES (?, ?, ?)', skills)
    return path


def render(dashboard):
    app = AppTest.from_file(str(dashboard / 'dashboard/app.py')).run(timeout=30)
    assert not app.exception
    return app


def chart(app, key):
    return next(json.loads(item.proto.spec) for item in app.get('plotly_chart')
                if item.proto.id.endswith('-' + key))


def metric(app, label):
    return next(item.value for item in app.metric if item.label == label)


def info_contains(app, text):
    assert any(text in item.value for item in app.info)


def test_single_date_charts_and_read_only_connection(dashboard, monkeypatch):
    path = snapshots(dashboard, [('2026-10-31', 100, 25)],
                     [('2026-10-31', 'Python', 10), ('2026-10-31', 'SQL', 0)])
    before = path.read_bytes()
    original_connect = sqlite3.connect
    connections = []

    def connect(*args, **kwargs):
        connections.append((args, kwargs))
        return original_connect(*args, **kwargs)

    monkeypatch.setattr(sqlite3, 'connect', connect)
    app = render(dashboard)
    assert connections and all('mode=ro' in args[0] and kwargs['uri'] for args, kwargs in connections)
    assert path.read_bytes() == before
    assert metric(app, 'Latest snapshot remote') == '25.0%'
    info_contains(app, 'Only one snapshot date')
    for key in ['history_jobs', 'history_remote', 'history_skills_chart']:
        assert chart(app, key)['data']
    assert len(chart(app, 'history_jobs')['data'][0]['x']) == 1
    assert len(chart(app, 'history_skills_chart')['data']) == 2
    assert sum('Arbeitnow sampled postings' in item.value for item in app.caption) >= 4
    assert metric(app, 'Job postings') == '1'
    assert len(app.dataframe[0].value) == 1


def test_multiple_dates_and_filters(dashboard):
    snapshots(dashboard, [('2026-10-29', 100, 20), ('2026-10-31', 200, 50)],
              [('2026-10-29', 'Python', 10), ('2026-10-31', 'Python', 30),
               ('2026-10-29', 'SQL', 0), ('2026-10-31', 'SQL', 5)])
    app = render(dashboard)
    assert metric(app, 'Snapshot dates') == '2'
    assert len(chart(app, 'history_jobs')['data'][0]['x']) == 2
    assert not any('Only one snapshot' in item.value for item in app.info)
    app.multiselect(key='history_skills').set_value(['SQL']).run()
    assert not app.exception
    assert [trace['name'] for trace in chart(app, 'history_skills_chart')['data']] == ['SQL']
    app.multiselect(key='history_skills').set_value([]).run()
    assert not app.exception
    info_contains(app, 'Select at least one skill')
    app.date_input(key='history_dates').set_value(('2026-10-29', '2026-10-29')).run()
    assert not app.exception
    assert metric(app, 'Latest snapshot jobs') == '100'
    assert metric(app, 'Latest snapshot remote') == '20.0%'
    app.date_input(key='history_dates').set_value(('2026-10-30', '2026-10-30')).run()
    assert not app.exception
    info_contains(app, 'No snapshots in this date range')


@pytest.mark.parametrize('state', ['missing_file', 'empty_market', 'missing_skills', 'empty_skills', 'zero_jobs', 'unknown_remote'])
def test_history_empty_states(dashboard, state):
    if state != 'missing_file':
        rows = [] if state == 'empty_market' else [('2026-10-31', 0 if state == 'zero_jobs' else 100,
                                                    None if state == 'unknown_remote' else 0)]
        snapshots(dashboard, rows, skill_table=state != 'missing_skills')
    app = render(dashboard)
    if state == 'missing_file':
        info_contains(app, 'No snapshots yet')
        assert not (dashboard / 'data/state/snapshots.sqlite').exists()
    elif state == 'empty_market':
        info_contains(app, 'No daily snapshots')
    elif state in ['missing_skills', 'empty_skills']:
        info_contains(app, 'No skill snapshots')
        assert chart(app, 'history_jobs')['data']
        assert metric(app, 'Latest snapshot remote') == '0.0%'
    else:
        assert metric(app, 'Latest snapshot remote') == 'Unavailable'
        info_contains(app, 'Remote-job percentages are unavailable')


def test_source_filters_and_attribution(dashboard):
    with duckdb.connect(str(dashboard / 'data/gold/job_market.duckdb')) as con:
        con.execute("INSERT INTO fact_job_postings VALUES ('jobicy', 'one', 'Remote role', 1, 1, TRUE, 'https://jobicy.com/jobs/one', '2026-10-31')")
    path = snapshots(dashboard, [('2026-10-29', 10, 2), ('2026-10-31', 30, 10)])
    with sqlite3.connect(path) as con:
        con.execute('CREATE TABLE daily_source_market_snapshots (snapshot_date TEXT, source TEXT, total_jobs INTEGER, remote_jobs INTEGER)')
        con.executemany('INSERT INTO daily_source_market_snapshots VALUES (?, ?, ?, ?)',
                        [('2026-10-31', 'arbeitnow', 10, 2), ('2026-10-31', 'jobicy', 20, 8)])
        con.execute('CREATE TABLE daily_source_skill_snapshots (snapshot_date TEXT, source TEXT, skill_name TEXT, job_count INTEGER)')
        con.executemany('INSERT INTO daily_source_skill_snapshots VALUES (?, ?, ?, ?)',
                        [('2026-10-31', 'arbeitnow', 'Python', 4), ('2026-10-31', 'jobicy', 'Python', 5)])
    app = render(dashboard)
    assert metric(app, 'Latest snapshot jobs') == '30'
    assert metric(app, 'Snapshot dates') == '1'  # Do not invent a legacy source breakdown.
    app.multiselect(key='sources').set_value(['jobicy']).run()
    assert not app.exception
    assert metric(app, 'Job postings') == '1'
    assert app.dataframe[0].value['Source'].to_list() == ['jobicy']
    assert app.dataframe[0].value['Job link'].to_list() == ['https://jobicy.com/jobs/one']
    app.multiselect(key='history_sources').set_value(['jobicy']).run()
    assert not app.exception
    assert metric(app, 'Latest snapshot jobs') == '20'
    assert metric(app, 'Latest snapshot remote') == '40.0%'
    assert any('Jobicy sampled postings' in item.value for item in app.caption)
    app.multiselect(key='history_sources').set_value([]).run()
    assert not app.exception
    info_contains(app, 'Select at least one historical source')
