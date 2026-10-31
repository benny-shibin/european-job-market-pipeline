"""Globe accuracy, palette switching and source availability integration tests."""
import pandas as pd
import pytest

from dashboard.visuals import globe_figure, map_jobs, palette, resolve_location
from tests.test_dashboard_history import dashboard, render, chart, metric


@pytest.mark.parametrize('location,name,precision', [
    ('Berlin', 'Berlin', 'City'), ('Berlin, Germany', 'Berlin', 'City'),
    ('Berlin Office', 'Berlin', 'City'), ('UK - London', 'London', 'City'),
    ('Paris, Île-de-France, France', 'Paris', 'City'), ('Germany', 'Germany', 'Country'),
    ('Zurich, Zürich, Switzerland', 'Zürich', 'City'),
])
def test_geographic_label_resolution(location, name, precision):
    point = resolve_location(location)
    assert point['name'] == name and point['precision'] == precision
    assert -90 <= point['lat'] <= 90 and -180 <= point['lon'] <= 180


@pytest.mark.parametrize('location', ['Europe', 'EMEA', 'Remote', 'Anywhere', '', None,
                                       'Berlin / Munich', 'Germany, Ireland, Spain, UK',
                                       'Paris, Germany', 'Berlin, Unmapped City', 'Unknown location'])
def test_ambiguous_and_regional_locations_are_not_pinned(location):
    assert resolve_location(location) is None


def test_globe_aggregates_distinct_source_job_identity():
    jobs = pd.DataFrame([
        dict(source='arbeitnow', job_id='1', location_name='Berlin', remote=True),
        dict(source='arbeitnow', job_id='1', location_name='Berlin', remote=True),
        dict(source='jobicy', job_id='1', location_name='Berlin, Germany', remote=False),
        dict(source='jobicy', job_id='2', location_name='Europe', remote=True),
    ])
    points, count = map_jobs(jobs)
    assert count == 2 and len(points) == 1
    assert points.iloc[0]['jobs'] == 2 and points.iloc[0]['remote_jobs'] == 1
    fig = globe_figure(points, palette())
    signals = next(trace for trace in fig.data if trace.name == 'Job locations')
    assert signals.customdata[0][2] == 2
    assert fig.layout.scene.xaxis.visible is False


def test_theme_toggle_and_globe_respond_to_filters(dashboard):
    app = render(dashboard)
    assert chart(app, 'jobs_globe')['layout']['paper_bgcolor'] == palette()['surface']
    assert any(trace.get('name') == 'Job locations' for trace in chart(app, 'jobs_globe')['data'])
    app.toggle(key='light_mode').set_value(True).run(timeout=30)
    assert not app.exception
    assert chart(app, 'jobs_globe')['layout']['paper_bgcolor'] == palette(True)['surface']
    assert chart(app, 'skills_chart')['layout']['font']['color'] == palette(True)['text']
    assert chart(app, 'skill_fingerprints')['data'][0]['type'] == 'heatmap'
    app.text_input(key='search').set_value('no matching title').run(timeout=30)
    assert not app.exception
    assert app.toggle(key='light_mode').value is True
    assert metric(app, 'Job postings') == '0'
    assert not any(trace.get('name') == 'Job locations' for trace in chart(app, 'jobs_globe')['data'])
    assert chart(app, 'jobs_globe')['data'][0]['type'] == 'surface'
    assert any('0 of 0 filtered postings mapped' in item.value for item in app.caption)


def test_streamlit_entrypoint_resolves_project_modules_from_isolated_path(dashboard):
    import os
    from pathlib import Path
    import subprocess
    import sys
    root = Path(__file__).resolve().parents[1]
    code = ("from streamlit.testing.v1 import AppTest; "
            f"app=AppTest.from_file({str(root / 'dashboard/app.py')!r}).run(timeout=30); "
            "assert not app.exception, app.exception")
    result = subprocess.run([sys.executable, '-I', '-c', code], cwd=dashboard,
                            env=os.environ | {'JOB_MARKET_DATA_DIR': str(dashboard / 'data')},
                            capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr


def test_motion_controls_and_new_chart_types_preserve_filters(dashboard):
    app = render(dashboard)
    assert chart(app, 'arrangement_chart')['data'][0]['type'] == 'treemap'
    assert any(trace['type'] == 'scatter' for trace in chart(app, 'skills_chart')['data'])
    assert len(chart(app, 'globe_locations')['data']) == 2  # Total and remote subset.
    app.toggle(key='animations').set_value(False).run(timeout=30)
    assert not app.exception
    assert chart(app, 'skills_chart')['layout']['transition']['duration'] == 0
    app.toggle(key='light_mode').set_value(True).run(timeout=30)
    assert not app.exception
    assert chart(app, 'skills_chart')['layout']['paper_bgcolor'] == palette(True)['surface']
    app.button[0].click().run(timeout=30)
    assert not app.exception
    assert app.toggle(key='animations').value is False
    assert app.toggle(key='light_mode').value is True
    assert metric(app, 'Job postings') == '1'
