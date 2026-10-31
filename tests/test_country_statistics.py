"""Single-country attribution, country summaries and globe labeling."""
import pandas as pd
import pytest

from dashboard.visuals import country_statistics, globe_figure, location_country, map_jobs, palette
from tests.test_dashboard_history import dashboard, render, chart


@pytest.mark.parametrize('location,country', [
    ('Berlin Office', 'Germany'), ('Munich / Berlin', 'Germany'),
    ('Reading, UK', 'United Kingdom'), ('Zoetermeer, Zuid-Holland, Nederland', 'Netherlands'),
    ('Germany, France', None), ('Europe, UK', None), ('Remote', None),
    ('Paris, Germany', None), ('London; Paris', None), ('Europe', None), (None, None),
])
def test_conservative_country_attribution(location, country):
    assert location_country(location) == country


def test_country_totals_deduplicate_jobs_and_keep_unknown_scopes():
    jobs = pd.DataFrame([
        dict(source='a', job_id='1', location_name='Berlin', remote=True, company_name='Acme'),
        dict(source='a', job_id='1', location_name='Berlin', remote=True, company_name='Acme'),
        dict(source='b', job_id='1', location_name='Germany', remote=False, company_name='Acme'),
        dict(source='b', job_id='2', location_name='Europe', remote=True, company_name='Other'),
        dict(source='b', job_id='3', location_name='Germany / France', remote=True, company_name='Other'),
    ])
    stats, unassigned = country_statistics(jobs)
    assert unassigned == 2 and len(stats) == 1
    row = stats.iloc[0]
    assert row.country == 'Germany' and row.jobs == 2 and row.companies == 1
    assert row.remote_jobs == 1 and row.remote_share == 50 and row.share == 50


def test_globe_closer_camera_and_country_labels(dashboard):
    app = render(dashboard)
    figure = chart(app, 'jobs_globe')
    assert figure['layout']['scene']['camera']['eye']['x'] < 1.2
    labels = next(trace for trace in figure['data'] if trace.get('name') == 'Country labels')
    assert 'Germany' in labels['text']
    assert chart(app, 'globe_locations')['data'][0]['y'] == ['Germany']
    assert 'Country' in app.table[0].value.columns
    assert not any(toggle.key == 'country_labels' for toggle in app.toggle)
    app.toggle(key='light_mode').set_value(True).run(timeout=30)
    assert not app.exception
    assert any(trace.get('name') == 'Country labels' for trace in chart(app, 'jobs_globe')['data'])


def test_nullable_remote_flags_have_valid_country_denominators():
    jobs = pd.DataFrame(dict(source=['a', 'a'], job_id=['1', '2'], location_name=['Germany', 'Berlin']))
    jobs['remote'] = pd.Series([True, pd.NA], dtype='boolean')
    stats, unassigned = country_statistics(jobs)
    assert unassigned == 0 and stats.iloc[0].jobs == 2
    assert stats.iloc[0].remote_jobs == 1 and stats.iloc[0].remote_share == 50


def test_all_country_labels_can_be_enabled(dashboard):
    app = render(dashboard)
    def label_count():
        return len(next(trace for trace in chart(app, 'jobs_globe')['data'] if trace.get('name') == 'Country labels')['text'])
    major = label_count()
    app.selectbox(key='globe_label_detail').set_value('All countries').run(timeout=30)
    assert not app.exception
    assert label_count() > major


def test_country_filter_includes_cities_and_country_wide_listings(dashboard):
    import duckdb
    from tests.test_location_normalization import add_location_variants
    add_location_variants(dashboard)
    with duckdb.connect(str(dashboard / 'data/gold/job_market.duckdb')) as con:
        con.execute("INSERT INTO dim_locations VALUES (5, 'Paris')")
        con.execute("INSERT INTO fact_job_postings VALUES ('arbeitnow', 'paris', 'Engineer', 1, 5, TRUE, 'https://example.com/paris', '2026-10-31')")
    app = render(dashboard)
    app.multiselect(key='countries').set_value(['Germany']).run(timeout=30)
    assert not app.exception
    assert app.metric[0].value == '4'
    assert app.table[0].value['Country'].to_list() == ['Germany']
    assert app.table[0].value['Postings'].to_list() == [4]
    app.button[0].click().run(timeout=30)
    assert not app.exception
    assert app.multiselect(key='countries').value == []
    assert app.metric[0].value == '5'


def test_country_filter_handles_older_normalizer_without_country_column(dashboard, monkeypatch):
    import dashboard.visuals as visuals
    original = visuals.normalize_job_locations

    def legacy_normalizer(jobs):
        return original(jobs).drop(columns=['country_name'], errors='ignore')

    monkeypatch.setattr(visuals, 'normalize_job_locations', legacy_normalizer)
    app = render(dashboard)
    assert app.multiselect(key='countries').options == ['Germany']
    app.multiselect(key='sources').set_value(['arbeitnow']).run(timeout=30)
    assert not app.exception
    app.multiselect(key='countries').set_value(['Germany']).run(timeout=30)
    assert not app.exception
    assert app.metric[0].value == '1'


def test_stale_visual_module_is_refreshed_before_rendering(dashboard, monkeypatch):
    import dashboard.visuals as visuals

    def old_globe_signature(points, colors):
        raise AssertionError('Stale globe function should be reloaded')

    monkeypatch.setattr(visuals, '_SOURCE_MTIME_NS', None)
    monkeypatch.setattr(visuals, 'globe_figure', old_globe_signature)
    app = render(dashboard)
    assert app.multiselect(key='countries').options == ['Germany']
    assert chart(app, 'jobs_globe')['data']


def test_country_percentage_highlights_include_unassigned_in_denominator(dashboard):
    import duckdb
    with duckdb.connect(str(dashboard / 'data/gold/job_market.duckdb')) as con:
        con.execute("INSERT INTO dim_locations VALUES (2, 'Germany'), (3, 'Paris'), (4, 'Europe')")
        con.execute("""INSERT INTO fact_job_postings VALUES
            ('arbeitnow', 'two', 'Engineer', 1, 2, FALSE, 'https://example.com/two', '2026-10-31'),
            ('jobicy', 'three', 'Engineer', 1, 3, TRUE, 'https://example.com/three', '2026-10-31'),
            ('jobicy', 'four', 'Engineer', 1, 4, TRUE, 'https://example.com/four', '2026-10-31')""")
    app = render(dashboard)
    figure = chart(app, 'globe_locations')
    assert figure['layout']['xaxis']['ticksuffix'] == '%'
    assert figure['data'][0]['text'] == ['25.0% · 1', '50.0% · 2']
    text = '\n'.join(item.value for item in app.markdown)
    assert '50.0<small>%</small>' in text
    assert '25.0 pp ahead of France' in text
    assert 'Unassigned / multiple countries' in text
    app.radio(key='country_measure').set_value('Posting count').run(timeout=30)
    assert not app.exception
    assert chart(app, 'globe_locations')['data'][0]['text'] == ['1 · 25.0%', '2 · 50.0%']
    app.multiselect(key='countries').set_value(['Germany']).run(timeout=30)
    assert not app.exception
    assert chart(app, 'globe_locations')['data'][0]['text'] == ['2 · 100.0%']
    assert 'pp ahead of France' not in '\n'.join(item.value for item in app.markdown)


def test_equal_country_shares_display_tie(dashboard):
    import duckdb
    with duckdb.connect(str(dashboard / 'data/gold/job_market.duckdb')) as con:
        con.execute("INSERT INTO dim_locations VALUES (2, 'France')")
        con.execute("INSERT INTO fact_job_postings VALUES ('jobicy', 'two', 'Engineer', 1, 2, TRUE, 'https://example.com/two', '2026-10-31')")
    app = render(dashboard)
    text = '\n'.join(item.value for item in app.markdown)
    assert 'Same share as Germany' in text
    assert 'pp ahead' not in text
