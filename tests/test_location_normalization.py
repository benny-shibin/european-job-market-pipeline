"""Location grouping must combine aliases without changing job identity/scope."""
import duckdb
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from dashboard.visuals import map_jobs, normalize_job_locations, normalize_location_label
from tests.test_dashboard_history import dashboard, chart, metric, render


@pytest.mark.parametrize('variants,label', [
    (['Berlin', ' BERLIN ', 'Berlin Office', 'Berlin HQ', 'Berlin, Berlin', 'Berlin, Germany', 'DE - Berlin', 'Berlin (Hybrid)', 'Berlin/Remote'], 'Berlin, Germany'),
    (['Munich', 'München', 'Munich Office', 'Munich, Bavaria', 'München, Bayern, Germany'], 'Munich, Germany'),
    (['Zurich', 'Zürich', 'Zuerich', 'Zurich HQ', 'Zurich, Zürich, Switzerland'], 'Zürich, Switzerland'),
    (['London', 'London Office', 'UK - London', 'London (UK)', 'London, England, United Kingdom', 'London, Greater London'], 'London, United Kingdom'),
    (['Amsterdam', 'Amsterdam office', 'Amsterdam, North Holland, Netherlands', 'Amsterdam, Noord-Holland, Nederland'], 'Amsterdam, Netherlands'),
    (['UK', 'U.K.', 'United Kingdom'], 'United Kingdom'),
    (['Germany', 'Deutschland', 'Germany (Remote)', 'Remote (Germany)', 'Germany (Hybrid)'], 'Germany'),
    (['Remote', 'Remote job', 'Homeoffice', 'Poste à distance', 'Remote/Home Based'], 'Remote / unspecified'),
    ([None, '', '  ', 'Unknown location'], 'Unknown location'),
    (['Germany, UK, Ireland', 'Ireland; United Kingdom; Deutschland', 'UK | Ireland | Germany | UK'], 'Germany / Ireland / United Kingdom'),
    (['UK (Reading)', 'Reading, UK', 'reading, United Kingdom'], 'Reading, United Kingdom'),
    (['veenendaal', ' VEENENDAAL ', 'Veenendaal Office'], 'Veenendaal'),
])
def test_equivalent_labels_group_idempotently(variants, label):
    for raw in variants:
        assert normalize_location_label(raw) == label
    assert normalize_location_label(label) == label


def test_country_names_do_not_resolve_to_station_aliases():
    from dashboard.visuals import place_index
    assert all(item['precision'] == 'Country' for item in place_index()['germany'])


def test_different_geographies_and_precision_do_not_collapse():
    assert normalize_location_label('Paris, Germany') != normalize_location_label('Paris, France')
    assert normalize_location_label('Berlin / Munich') != normalize_location_label('Berlin')
    assert normalize_location_label('Germany') != normalize_location_label('Berlin')
    assert normalize_location_label('London area') != normalize_location_label('London')
    assert normalize_location_label('Europe') != normalize_location_label('Germany')
    assert normalize_location_label('Berlin, Unmapped City') != normalize_location_label('Berlin')


def test_preserves_raw_values_and_composite_job_identity():
    jobs = pd.DataFrame([
        dict(source='arbeitnow', job_id='same', location_name='Berlin Office', remote=False),
        dict(source='jobicy', job_id='same', location_name='Berlin, Germany', remote=True),
        dict(source='jobicy', job_id='two', location_name='Europe', remote=True),
    ])
    normalized = normalize_job_locations(jobs)
    assert len(normalized) == len(jobs)
    assert normalized['raw_location_name'].to_list() == jobs['location_name'].to_list()
    assert jobs['location_name'].to_list()[0] == 'Berlin Office'  # Input unchanged.
    pd.testing.assert_frame_equal(normalize_job_locations(normalized), normalized)
    points, mapped = map_jobs(normalized)
    assert mapped == 2 and len(points) == 1 and points.iloc[0]['jobs'] == 2
    assert normalized['remote'].to_list() == [False, True, True]


def add_location_variants(dashboard):
    with duckdb.connect(str(dashboard / 'data/gold/job_market.duckdb')) as con:
        con.execute("INSERT INTO dim_locations VALUES (2, 'Berlin Office'), (3, 'Berlin, Germany'), (4, 'Germany')")
        con.execute("""INSERT INTO fact_job_postings VALUES
            ('arbeitnow', 'two', 'Engineer', 1, 2, TRUE, 'https://example.com/two', '2026-10-31'),
            ('jobicy', 'one', 'Engineer', 1, 3, TRUE, 'https://example.com/other', '2026-10-31'),
            ('arbeitnow', 'country', 'Engineer', 1, 4, FALSE, 'https://example.com/country', '2026-10-31')""")


def test_dashboard_filter_chart_and_listing_share_canonical_locations(dashboard):
    add_location_variants(dashboard)
    app = render(dashboard)
    assert app.multiselect(key='locations').options == ['Berlin, Germany', 'Germany']
    assert metric(app, 'Locations') == '2'
    assert set(app.dataframe[0].value['Original location']) == {'Berlin', 'Berlin Office', 'Berlin, Germany', 'Germany'}
    app.multiselect(key='locations').set_value(['Berlin, Germany']).run(timeout=30)
    assert not app.exception
    assert metric(app, 'Job postings') == '3'
    assert metric(app, 'Locations') == '1'
    assert app.dataframe[0].value['Location'].unique().tolist() == ['Berlin, Germany']
    assert chart(app, 'locations_chart')['data'][0]['y'] == ['Berlin, Germany']
    points = next(trace for trace in chart(app, 'jobs_globe')['data'] if trace.get('name') == 'Job locations')
    assert points['customdata'][0][2] == 3
    with duckdb.connect(str(dashboard / 'data/gold/job_market.duckdb'), read_only=True) as con:
        assert con.execute('SELECT COUNT(*) FROM fact_job_postings').fetchone()[0] == 4
        assert con.execute("SELECT location_name FROM dim_locations WHERE location_key=2").fetchone()[0] == 'Berlin Office'


def test_existing_raw_filter_selection_is_migrated(dashboard):
    add_location_variants(dashboard)
    app = AppTest.from_file(str(dashboard / 'dashboard/app.py'))
    app.session_state['locations'] = ['Berlin', 'Berlin Office']
    app.run(timeout=30)
    assert not app.exception
    assert app.multiselect(key='locations').value == ['Berlin, Germany']
    assert metric(app, 'Job postings') == '3'
