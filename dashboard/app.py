from pathlib import Path
import sqlite3
import json
import os
import sys
import importlib
import base64
from html import escape
import duckdb
import pandas as pd
import polars as pl
import plotly.express as px
import streamlit as st
import plotly.graph_objects as go

# The Streamlit executable adds dashboard/ to sys.path; add the checkout root
# so both `streamlit run` and `python -m streamlit` resolve project modules.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import dashboard.visuals as visuals
# Streamlit reruns app.py but can retain imported helper modules after edits.
# Refresh only when their source changes, preserving caches on ordinary reruns.
if getattr(visuals, '_SOURCE_MTIME_NS', None) != Path(visuals.__file__).stat().st_mtime_ns:
    importlib.reload(visuals)

from dashboard.visuals import palette, map_jobs, globe_figure, normalize_job_locations, normalize_location_label, country_statistics, location_country
from src.job_market.quality.source_health import audit_sources

DATA_DIR = Path(os.environ.get('JOB_MARKET_DATA_DIR', Path(__file__).resolve().parents[1] / 'data')).resolve()
DB_PATH = DATA_DIR / 'gold' / 'job_market.duckdb'
SNAPSHOT_PATH = DB_PATH.parents[1] / 'state' / 'snapshots.sqlite'

LOGO_PATH = Path(__file__).with_name('assets') / 'orbit.svg'
st.set_page_config(page_title='Orbit · European Job Intelligence', page_icon=str(LOGO_PATH), layout='wide', initial_sidebar_state='expanded')
logo_uri = 'data:image/svg+xml;base64,' + base64.b64encode(LOGO_PATH.read_bytes()).decode()
st.sidebar.markdown(f'<div class="brand"><img src="{logo_uri}" alt="Orbit logo"><div><div class="brand-name">ORBIT</div><div class="brand-subtitle">Job intelligence</div></div></div>', unsafe_allow_html=True)
light_mode = st.sidebar.toggle('Light mode', value=False, key='light_mode', help='Switch the dashboard and charts between light and dark palettes.')
animations = st.sidebar.toggle('Animations', value=True, key='animations', help='Enable decorative motion and chart transitions.')
THEME = palette(light_mode)
variables = dict(bg=THEME['bg'], surface=THEME['surface'], raised=THEME['raised'], text=THEME['text'],
                 muted=THEME['muted'], border=THEME['border'], accent=THEME['accent'],
                 header=THEME['bg'] + 'ee',
                 shadow='#14304a08' if light_mode else '#00000022',
                 **{'accent-wash': THEME['accent'] + '10',
                    'grid-wash': THEME['blue'] + '08',
                    'edge-glow': THEME['accent'] + ('25' if light_mode else '40'),
                    'motion-state': 'running' if animations else 'paused',
                    'logo-brightness': '.65' if light_mode else '1',
                    'reveal-duration': '650ms' if animations else '0ms'})

css_variables = ';'.join(f'--{name}:{value}' for name, value in variables.items())
st.markdown(f"<style>:root{{{css_variables};color-scheme:{'light' if light_mode else 'dark'}}}" + Path(__file__).with_name('styles.css').read_text() + '</style>', unsafe_allow_html=True)

if not animations:
    st.markdown('<style>*,*::before,*::after{animation:none!important;transition:none!important}</style>', unsafe_allow_html=True)

FILTER_KEYS = ['search', 'locations', 'companies', 'skills', 'arrangement', 'sources', 'countries']


def reset_filters():
    for key in FILTER_KEYS:
        st.session_state.pop(key, None)


@st.cache_data(ttl=300)
def load_data(db_mtime):
    with duckdb.connect(str(DB_PATH), read_only=True) as con:
        jobs = con.execute('''
            SELECT f.source, f.job_id, f.title, c.company_name,
                   l.location_name, f.remote, f.url, f.ingested_at
            FROM fact_job_postings f
            LEFT JOIN dim_companies c ON f.company_key = c.company_key
            LEFT JOIN dim_locations l ON f.location_key = l.location_key
        ''').df()
        skills = con.execute('''
            SELECT b.source, b.job_id, s.skill_name
            FROM bridge_job_skills b
            JOIN dim_skills s ON b.skill_key = s.skill_key
        ''').df()
    return jobs, skills

if not DB_PATH.exists():
    st.error('Gold database not found. Run `python -m src.job_market.transformations.silver_2_gold` from the project root.')
    st.stop()

try:
    jobs, skills = load_data(DB_PATH.stat().st_mtime_ns)
except Exception as exc:
    st.error(f'Could not read Gold database: {exc}')
    st.stop()

if jobs.empty:
    st.warning('No jobs available in the Gold layer.')
    st.stop()

jobs['company_name'] = jobs['company_name'].fillna('Unknown company')
jobs = normalize_job_locations(jobs)
# Derive the filter column here on every rerun, including when a long-lived
# Streamlit process still holds an older location-normalization helper.
raw_locations = jobs['raw_location_name'] if 'raw_location_name' in jobs else jobs['location_name']
jobs['country_name'] = raw_locations.map(location_country)
jobs['title'] = jobs['title'].fillna('Untitled role')
jobs['remote_label'] = jobs['remote'].map({True: 'Remote', False: 'Not marked remote'}).fillna('Unknown')
jobs['ingested_at'] = pd.to_datetime(jobs['ingested_at'], utc=True, errors='coerce')

with st.sidebar:
    st.markdown('<div class="section-label">Refine your orbit</div>', unsafe_allow_html=True)
    st.caption('Filter every live-data view below.')
    source_filter = st.multiselect('Sources', sorted(jobs['source'].unique()), key='sources')
    country_filter = st.multiselect('Countries', sorted(jobs['country_name'].dropna().unique()), key='countries',
                                    help='Includes city and country-wide postings. Regional and multi-country listings remain unassigned.')
    search = st.text_input('Search job titles or companies', placeholder='e.g. Data Engineer', key='search')
    with st.expander('More filters', expanded=False):
        location_options = sorted(jobs['location_name'].unique())
        if 'locations' in st.session_state:
            selected_locations = list(dict.fromkeys(normalize_location_label(value) for value in st.session_state['locations']))
            selected_locations = [value for value in selected_locations if value in location_options]
            if selected_locations != st.session_state['locations']:
                st.session_state['locations'] = selected_locations
        location_filter = st.multiselect('Locations', location_options, key='locations',
                                         help='Equivalent city/country names and office labels are grouped. Original source text is available in listings.')
        company_filter = st.multiselect('Companies', sorted(jobs['company_name'].unique()), key='companies')
        skill_filter = st.multiselect('Skills (match any)', sorted(skills['skill_name'].dropna().unique()), key='skills')
        remote_filter = st.selectbox('Work arrangement', ['All', 'Remote', 'Not marked remote', 'Unknown'], key='arrangement')
    st.button('Reset filters', width='stretch', on_click=reset_filters)
    st.divider()
    st.caption('Sources: ' + ', '.join(sorted(jobs['source'].unique())) + ' · Local DuckDB')
    if 'jobicy' in jobs['source'].values:
        st.caption('Jobicy listings: original source [Jobicy](https://jobicy.com), with canonical links preserved.')
    st.caption('Results describe the collected sample, not the entire European job market.')

filtered = jobs.copy()
if source_filter:
    filtered = filtered[filtered['source'].isin(source_filter)]
if country_filter:
    filtered = filtered[filtered['country_name'].isin(country_filter)]
if search.strip():
    query = search.strip()
    filtered = filtered[
        filtered['title'].str.contains(query, case=False, regex=False, na=False)
        | filtered['company_name'].str.contains(query, case=False, regex=False, na=False)
    ]
if location_filter:
    filtered = filtered[filtered['location_name'].isin(location_filter)]
if company_filter:
    filtered = filtered[filtered['company_name'].isin(company_filter)]
if remote_filter != 'All':
    filtered = filtered[filtered['remote_label'] == remote_filter]
if skill_filter:
    matching = skills[skills['skill_name'].isin(skill_filter)][['source', 'job_id']].drop_duplicates()
    filtered = filtered.merge(matching, on=['source', 'job_id'], how='inner')

filtered_skills = skills.merge(filtered[['source', 'job_id']], on=['source', 'job_id'], how='inner')

st.markdown('<div class="masthead"><span>ORBIT <b> / </b> INTELLIGENCE WORKSPACE</span><a href="#opportunity-board">Explore opportunities ↗</a></div>', unsafe_allow_html=True)
hero_left, hero_right = st.columns([.9, 1.5], gap='large')
with hero_right:
    globe_slot = st.empty()
with hero_left:
    st.markdown('<div class="eyebrow"><span class="signal-dot"></span> THE EUROPEAN HIRING OBSERVATORY</div>', unsafe_allow_html=True)
    st.markdown('<h1 class="hero-title">Find your<br>next <em>orbit.</em></h1>', unsafe_allow_html=True)
    if (DATA_DIR / 'demo.json').exists():
        st.info('Synthetic demonstration data. These listings and historical counts are generated examples, not observed market activity.')
    st.markdown('<p class="hero-copy">A different perspective on work. Trace the places, people and skills shaping your collected job market.</p>', unsafe_allow_html=True)
    latest = jobs['ingested_at'].max()
    freshness = f"Latest content observation {latest.strftime('%d %b %Y · %H:%M UTC')}" if pd.notna(latest) else 'Collection time unavailable'
    st.markdown(f'<div class="status">{freshness} &nbsp; · &nbsp; {len(filtered):,} of {len(jobs):,} listings</div>', unsafe_allow_html=True)

    with st.container(key='hero_metrics'):
        m1, m2 = st.columns(2)
        m3, m4 = st.columns(2)
        m1.metric('Job postings', f'{len(filtered):,}', help='Listings matching your current filters.')
        m2.metric('Hiring companies', f'{filtered["company_name"].replace("Unknown company", pd.NA).nunique():,}')
        m3.metric('Locations', f'{filtered["location_name"].replace(["Unknown location", "Remote / unspecified"], pd.NA).nunique():,}',
                  help='Distinct normalized geographic labels; city and country scopes remain separate.')
        remote_count = int(filtered['remote'].eq(True).sum())
        remote_share = remote_count / len(filtered) if len(filtered) else 0
        m4.metric('Marked remote', f'{remote_share:.0%}', help=f'{remote_count:,} postings explicitly marked remote.')
    st.write('')
    status_path = DB_PATH.parents[1] / 'state/latest_run.json'
    if status_path.exists():
        try:
            run_status = json.loads(status_path.read_text())
            refreshed = pd.to_datetime(run_status.get('finished_at') or run_status['started_at'], utc=True)
            st.caption(f"Pipeline {run_status['status']} · {refreshed:%d %b %Y · %H:%M UTC}")
            if run_status['status'] == 'failed':
                st.warning('Latest pipeline run failed. Displayed data may come from an earlier successful stage. ' + run_status.get('error', ''))
        except (OSError, ValueError, KeyError):
            st.caption('Pipeline run status is unavailable.')

    if filtered.empty:
        st.info('No listings match this combination. Broaden your search or reset the sidebar filters.')

COLORS = {'Remote': THEME['accent'], 'Not marked remote': THEME['blue'], 'Unknown': '#d69b54'}


def style_chart(fig, height=410):
    fig.update_layout(
        template='plotly_white' if light_mode else 'plotly_dark',
        paper_bgcolor=THEME['surface'], plot_bgcolor=THEME['surface'],
        colorway=[THEME['accent'], THEME['blue'], THEME['purple'], '#e4a45e', '#dc729c', '#6fb6c0'],
        font=dict(family='Inter, system-ui, sans-serif', color=THEME['text'], size=13),
        height=height, margin=dict(l=16, r=36, t=24, b=45),
        xaxis=dict(gridcolor=THEME['grid'], zeroline=False, title_font_size=12, tickfont=dict(color=THEME['muted'])),
        yaxis=dict(showgrid=False, zeroline=False, automargin=True, tickfont=dict(color=THEME['text'], size=12)),
        showlegend=False, hoverlabel=dict(bgcolor=THEME['hover'], font=dict(color=THEME['hover_text'], size=13)),
        transition=dict(duration=550 if animations else 0, easing='cubic-in-out'),
    )
    return fig


def render_chart(fig, key, height=410):
    st.plotly_chart(style_chart(fig, height), width='stretch', key=key, theme=None,
                    config={'displaylogo': False, 'scrollZoom': False,
                            'modeBarButtonsToRemove': ['lasso2d', 'select2d']})


def ranking_chart(counts, category, color, key, height=410):
    if counts.empty:
        st.info('No data available for these filters.')
        return
    counts = counts.sort_values('jobs', ascending=True)
    counts['share'] = counts['jobs'] / len(filtered)
    fig = go.Figure(go.Bar(x=counts['jobs'], y=counts[category], orientation='h',
                          marker=dict(color=THEME['raised'], cornerradius=3), width=.18, hoverinfo='skip'))
    fig.add_trace(go.Scatter(x=counts['jobs'], y=counts[category], mode='markers+text',
                            marker=dict(size=11, color=color, line=dict(width=2, color=THEME['surface'])),
                            text=counts['jobs'], textposition='middle right', cliponaxis=False,
                            customdata=counts[['share']], textfont=dict(color=THEME['text'], size=12),
                            hovertemplate='<b>%{y}</b><br>%{x:,} postings<br>%{customdata[0]:.1%} of filtered listings<extra></extra>'))
    fig.update_layout(xaxis_title='Sampled postings', yaxis_title='')
    fig.update_xaxes(range=[0, counts['jobs'].max() * 1.22], dtick=1 if counts['jobs'].max() < 8 else None)
    render_chart(fig, key, height)


def render_history():
    st.subheader('Historical trends')
    st.caption('Source samples · UTC snapshot dates · independent of the listing filters in the sidebar')
    st.caption('Changes describe the collected sample and do not establish whole-market hiring trends.')
    st.caption('Snapshots preserve daily aggregates of accumulated last-observed jobs, not complete historical job records or counts of newly posted jobs.')
    if not SNAPSHOT_PATH.exists():
        st.info('No snapshots yet. Run `python -m src.job_market.analytics.daily_snapshots` from the project root.')
        return
    try:
        with sqlite3.connect(f'{SNAPSHOT_PATH.as_uri()}?mode=ro', uri=True) as con:
            market = pd.read_sql_query('SELECT * FROM daily_market_snapshots ORDER BY snapshot_date', con)
            tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            history = (pd.read_sql_query('SELECT * FROM daily_skill_snapshots ORDER BY snapshot_date, skill_name', con)
                       if 'daily_skill_snapshots' in tables else pd.DataFrame(columns=['snapshot_date', 'skill_name', 'job_count']))
            source_market = (pd.read_sql_query('SELECT * FROM daily_source_market_snapshots ORDER BY snapshot_date', con)
                             if 'daily_source_market_snapshots' in tables else pd.DataFrame())
            source_skills = (pd.read_sql_query('SELECT * FROM daily_source_skill_snapshots ORDER BY snapshot_date', con)
                             if 'daily_source_skill_snapshots' in tables else pd.DataFrame())
    except (sqlite3.Error, pd.errors.DatabaseError) as exc:
        st.warning(f'Could not read snapshots: {exc}')
        return
    if market.empty:
        st.info('No daily snapshots available yet.')
        return

    sample_label = 'Arbeitnow sampled postings'
    if not source_market.empty:
        selected_sources = st.multiselect('Historical sources', sorted(source_market['source'].unique()),
                                           default=sorted(source_market['source'].unique()), key='history_sources')
        if not selected_sources:
            st.info('Select at least one historical source.')
            return
        sample_label = ', '.join({'arbeitnow': 'Arbeitnow', 'jobicy': 'Jobicy'}[source] for source in selected_sources) + ' sampled postings'
        included = source_market[source_market['source'].isin(selected_sources)]
        market = included.groupby('snapshot_date', as_index=False).agg(
            total_jobs=('total_jobs', 'sum'), remote_jobs=('remote_jobs', lambda values: values.sum(min_count=len(values))))
        history = (source_skills[source_skills['source'].isin(selected_sources)]
                   .groupby(['snapshot_date', 'skill_name'], as_index=False)['job_count'].sum()
                   if not source_skills.empty else pd.DataFrame(columns=['snapshot_date', 'skill_name', 'job_count']))
        st.caption('Source-filtered history starts when source-level snapshots were introduced; earlier aggregate-only dates are excluded.')
    st.caption(sample_label + ' · accumulated last-observed listings')
    market['snapshot_date'] = pd.to_datetime(market['snapshot_date'])
    history['snapshot_date'] = pd.to_datetime(history['snapshot_date'])
    first, last = market['snapshot_date'].min().date(), market['snapshot_date'].max().date()
    dates = st.date_input('Snapshot date range (UTC)', value=(first, last),
                          min_value=first, max_value=last, key='history_dates')
    if len(dates) != 2:
        st.info('Select both a start and an end date.')
        return
    start, end = map(pd.Timestamp, dates)
    market = market[market['snapshot_date'].between(start, end)].copy()
    history = history[history['snapshot_date'].between(start, end)].copy()
    if market.empty:
        st.info('No snapshots in this date range.')
        return
    if len(market) == 1:
        st.info('Only one snapshot date is available in this range. Points show daily totals; trends will appear after more daily snapshots are collected.')
    remote_jobs = pd.to_numeric(market['remote_jobs'], errors='coerce')
    market['remote_percentage'] = remote_jobs.div(market['total_jobs'].where(market['total_jobs'].gt(0))).mul(100)
    latest_snapshot = market.iloc[-1]
    a, b, c = st.columns(3)
    a.metric('Snapshot dates', len(market))
    b.metric('Latest snapshot jobs', f"{latest_snapshot['total_jobs']:,}")
    percentage = latest_snapshot['remote_percentage']
    c.metric('Latest snapshot remote', f'{percentage:.1f}%' if pd.notna(percentage) else 'Unavailable')
    st.caption(f"Latest selected snapshot: {latest_snapshot['snapshot_date']:%d %b %Y} (UTC)")

    def trend_chart(frame, y, label, key, color=None):
        fig = px.line(frame, x='snapshot_date', y=y, color=color, markers=True,
                      color_discrete_sequence=[THEME['accent'], THEME['blue'], THEME['purple'], '#d4944a', '#ce638e', '#3c9baa'],
                      labels={'snapshot_date': 'Snapshot date (UTC)', y: label, 'skill_name': 'Skill'})
        fig = style_chart(fig)
        fig.update_traces(line=dict(width=3), marker=dict(size=7), connectgaps=False)
        if color is None:
            fig.update_traces(fill='tozeroy', fillcolor=THEME['accent'] + '10')
        fig.update_layout(showlegend=color is not None, hovermode='x unified',
                          legend=dict(orientation='h', y=1.12, x=0, title_text=''))
        fig.update_xaxes(type='date', tickformat='%d %b %Y',
                         dtick=86400000 if (end - start).days <= 14 else None)
        fig.update_yaxes(rangemode='tozero')
        st.plotly_chart(fig, width='stretch', key=key, theme=None, config={'displaylogo': False})

    st.subheader('Daily job postings')
    st.caption(sample_label + ' · daily collected totals')
    trend_chart(market, 'total_jobs', 'Collected job postings', 'history_jobs')
    st.subheader('Daily remote-job percentage')
    st.caption(sample_label + ' · share explicitly marked remote')
    st.caption('Postings explicitly marked remote ÷ total collected postings. Missing remote counts and zero-job days have no percentage.')
    if market['remote_percentage'].notna().any():
        trend_chart(market, 'remote_percentage', 'Remote postings (%)', 'history_remote')
    else:
        st.info('Remote-job percentages are unavailable for this range.')

    st.subheader('Skill demand over time')
    st.caption(sample_label + ' · daily mentions by skill')
    options = sorted(history['skill_name'].unique())
    selected = st.multiselect('Historical skills', options,
                              default=[skill for skill in ['Python', 'SQL', 'AWS', 'Azure', 'Docker', 'Kubernetes'] if skill in options],
                              key='history_skills')
    if history.empty:
        st.info('No skill snapshots in this range. Run the snapshot step to capture skill counts.')
    elif not selected:
        st.info('Select at least one skill to compare.')
    else:
        # Missing skill/date combinations remain unknown rather than becoming zero.
        series = history.pivot(index='snapshot_date', columns='skill_name', values='job_count')
        series = series.reindex(index=market['snapshot_date'], columns=selected)
        series = series.rename_axis('snapshot_date').reset_index().melt(
            id_vars='snapshot_date', var_name='skill_name', value_name='job_count')
        trend_chart(series, 'job_count', 'Job postings mentioning skill', 'history_skills_chart', color='skill_name')


st.write('')
with globe_slot.container():
    st.markdown('<div class="globe-chrome"><span>01 / GEOGRAPHIC SIGNALS</span><span class="orbit-indicator">● OBSERVED SAMPLE</span></div>', unsafe_allow_html=True)
    st.subheader('Opportunity, in orbit')
    st.caption('Drag to rotate · scroll to zoom · hover a signal to explore')
    points, mapped_count = map_jobs(filtered)
    label_detail = st.selectbox('Label detail', ['Major countries', 'All countries'], key='globe_label_detail',
                                label_visibility='collapsed', help='Major countries reduces overlap; choose all labels after zooming in.')
    st.plotly_chart(globe_figure(points, THEME, label_detail=label_detail), width='stretch', key='jobs_globe', theme=None,
                    config={'displaylogo': False, 'scrollZoom': True, 'displayModeBar': False})
    st.caption(f'{mapped_count:,} of {len(filtered):,} filtered postings mapped · {len(filtered) - mapped_count:,} lack an unambiguous known location. Circles mark cities; diamonds mark countries. Locations are approximate, not exact offices. Region-only and multi-location postings remain unplaced.')
st.markdown('<div class="section-rule"><span>01 / COUNTRY REACH</span><span>EXPLORE THE DISTRIBUTION ↓</span></div>', unsafe_allow_html=True)
country_left, country_right = st.columns([1.3, 1], gap='large')
with country_left:
    st.subheader('Hiring signals by country')
    st.caption('Top 8 countries · distinct sampled postings · city and country labels rolled up together')
    countries, unassigned_count = country_statistics(filtered)
    if not countries.empty:
        top = countries.head(8).copy()
        top['label'] = top['country']
        top = top.sort_values('jobs')
        fig = go.Figure(go.Bar(x=top['jobs'], y=top['label'], orientation='h', name='All postings',
            marker=dict(color=THEME['raised'], cornerradius=5), text=top['jobs'], textposition='outside',
            cliponaxis=False, customdata=top[['remote_share']],
            hovertemplate='<b>%{y}</b><br>%{x:,} sampled postings<br>%{customdata[0]:.1f}% marked remote<extra></extra>'))
        fig.add_trace(go.Bar(x=top['remote_jobs'], y=top['label'], orientation='h', name='Marked remote',
                            marker=dict(color=THEME['accent'], cornerradius=5),
                            hovertemplate='<b>%{y}</b><br>%{x:,} explicitly remote postings<extra></extra>'))
        fig = style_chart(fig, 380)
        fig.update_layout(barmode='overlay', showlegend=True,
                          legend=dict(orientation='h', y=1.15, x=0), xaxis_title='Sampled postings')
        fig.update_xaxes(range=[0, top['jobs'].max() * 1.2])
        st.plotly_chart(fig, width='stretch', key='globe_locations', theme=None, config={'displaylogo': False})
    else:
        st.info('No single country can be identified for these filters. Listings remain available below.')
with country_right:
    st.markdown('<div class="panel-kicker">YOUR SAMPLE, AT A GLANCE</div>', unsafe_allow_html=True)
    if not countries.empty:
        leader = countries.iloc[0]
        st.markdown(f'<div class="insight-lead"><span>STRONGEST COUNTRY SIGNAL</span><h2>{escape(leader["country"])}</h2><p><strong>{int(leader["jobs"]):,}</strong> postings · {leader["share"]:.1f}% of filtered listings</p></div>', unsafe_allow_html=True)
    st.markdown('<div class="section-label">Sources in view</div>', unsafe_allow_html=True)
    for source, count in filtered['source'].value_counts().items():
        st.markdown(f'<div class="source-card"><div><div class="source-name">{escape(source.title())}</div><div class="source-detail">Original source attribution preserved</div></div><span class="source-badge">{count:,} postings</span></div>', unsafe_allow_html=True)

st.subheader('Country statistics')
st.caption(f'{int(countries["jobs"].sum()):,} postings assigned to {len(countries):,} countries · {unassigned_count:,} regional, multi-country or unknown postings remain unassigned. Each source/job counts once. Country labels describe source locations or eligibility, not confirmed office addresses.')
if not countries.empty:
    summary = countries.rename(columns={'country': 'Country', 'jobs': 'Postings', 'remote_jobs': 'Remote postings',
                                       'companies': 'Companies', 'remote_share': 'Remote share (%)', 'share': 'Share of filtered postings (%)'})
    with st.expander('Explore country totals, companies and remote share', expanded=False):
        st.table(summary.style.format({'Remote share (%)': '{:.1f}', 'Share of filtered postings (%)': '{:.1f}'}))
else:
    st.info('Country statistics will appear when listings have an identifiable single-country scope.')

st.markdown('<div class="section-rule"><span>02 / THE SIGNAL LAB</span><span>SKILLS · ORGANISATIONS · TIME</span></div>', unsafe_allow_html=True)
overview, geography, historical = st.tabs(['Skills & work patterns', 'Companies & locations', 'Historical trends'])
with historical:
    render_history()
with overview:
    left, right = st.columns([1.4, 1], gap='large')
    with left:
        st.subheader('Skills in demand')
        st.caption('Top 12 dictionary-matched skills · a posting can mention multiple skills')
        counts = (filtered_skills.drop_duplicates(['source', 'job_id', 'skill_name'])
                  .groupby('skill_name').size().nlargest(12).rename('jobs').reset_index())
        ranking_chart(counts, 'skill_name', THEME['accent'], 'skills_chart', 460)
    with right:
        st.subheader('How work is arranged')
        st.caption('Remote status as reported by the source')
        arrangements = filtered['remote_label'].value_counts().rename_axis('arrangement').reset_index(name='jobs')
        if not arrangements.empty:
            fig = px.treemap(arrangements, path=['arrangement'], values='jobs', color='arrangement',
                             color_discrete_map=COLORS)
            fig.update_traces(textinfo='label+value+percent root', textfont=dict(size=17, color=THEME['bg']),
                              marker=dict(line=dict(color=THEME['surface'], width=7)),
                              hovertemplate='<b>%{label}</b><br>%{value:,} sampled postings<extra></extra>')
            st.plotly_chart(style_chart(fig, 460), width='stretch', key='arrangement_chart', theme=None,
                            config={'displayModeBar': False})
        else:
            st.info('No work arrangement data to display.')
    st.subheader('Skill fingerprints')
    st.caption('Share of filtered postings mentioning each skill within each source · hover for counts · a posting can mention multiple skills')
    if not filtered_skills.empty and not filtered.empty:
        source_totals = filtered.groupby('source').size()
        skill_counts = filtered_skills.drop_duplicates(['source', 'job_id', 'skill_name']).groupby(['source', 'skill_name']).size()
        top_skills = skill_counts.groupby('skill_name').sum().nlargest(10).index
        matrix = skill_counts.unstack(fill_value=0).reindex(index=source_totals.index, columns=top_skills, fill_value=0)
        shares = matrix.div(source_totals, axis=0).mul(100)
        fig = go.Figure(go.Heatmap(z=shares.to_numpy(), x=shares.columns, y=[name.title() for name in shares.index],
            customdata=matrix.to_numpy(), zmin=0, zmax=100,
            colorscale=[[0, THEME['raised']], [.5, THEME['blue']], [1, THEME['accent']]],
            colorbar=dict(title='Share', ticksuffix='%', thickness=10, len=.8),
            hovertemplate='<b>%{y} · %{x}</b><br>%{customdata} postings · %{z:.1f}% of this source<extra></extra>',
            xgap=5, ygap=5))
        render_chart(fig, 'skill_fingerprints', max(220, len(shares) * 55 + 120))
    else:
        st.info('No skill mentions available for a source comparison.')
with geography:
    left, right = st.columns(2, gap='large')
    with left:
        st.subheader('Who is hiring')
        st.caption('Top 10 companies by observed job postings')
        counts = (filtered.loc[filtered['company_name'] != 'Unknown company', 'company_name']
                  .value_counts().head(10).rename_axis('company').reset_index(name='jobs'))
        ranking_chart(counts, 'company', THEME['purple'], 'companies_chart', 460)
    with right:
        st.subheader('Where opportunities cluster')
        st.caption('Top 10 normalized locations · city and country scopes kept separate')
        counts = (filtered.loc[~filtered['location_name'].isin(['Unknown location', 'Remote / unspecified']), 'location_name']
                  .value_counts().head(10).rename_axis('location').reset_index(name='jobs'))
        ranking_chart(counts, 'location', THEME['blue'], 'locations_chart', 460)

with st.expander('Source connections & ingestion health'):
    st.caption('Checks saved Bronze pages, fetch manifests, observed IDs and Silver/Gold consistency. This audit reads local data without making new API requests.')
    try:
        health = audit_sources(DATA_DIR)
        for source in health['sources']:
            connected = source['status'] == 'verified'
            detail = f"{source['gold_jobs']:,} warehouse postings"
            run = source.get('latest_run')
            if run:
                detail += f" · latest fetch {source.get('latest_fetched_records', 0):,} records across {run['pages']} pages"
            if source.get('reason'):
                detail = source['reason']
            badge = {'verified': 'Verified', 'needs_attention': 'Check data', 'not_ingested': 'Not ingested', 'access_required': 'Access required'}[source['status']]
            st.markdown(f'<div class="source-card"><div><div class="source-name">{escape(source["name"])}</div><div class="source-detail">{escape(detail)}</div></div><span class="source-badge {"" if connected else "pending"}">{badge}</span></div>', unsafe_allow_html=True)
            if run:
                st.caption(f"Last fetch: {run['started_at']} · {'complete traversal' if run['pagination_complete'] else 'page-capped sample'}")
            failed = [key for key, passed in source.get('checks', {}).items() if not passed]
            if failed:
                st.warning(source['name'] + ': ' + ', '.join(failed))
            if source.get('documentation'):
                st.markdown(f'[Official {source["name"]} integration documentation]({source["documentation"]})')
    except (sqlite3.Error, duckdb.Error, OSError, ValueError, KeyError, pl.exceptions.PolarsError) as exc:
        st.info(f'Ingestion audit unavailable: {exc}')

with st.expander('Job lifecycle observations'):
    st.caption('Observed and not observed refer to fetch visibility. Missing listings are never assumed closed. Partial or failed fetches do not establish absence.')
    lifecycle_path = DB_PATH.parents[1] / 'state/jobs.sqlite'
    if lifecycle_path.exists():
        try:
            with sqlite3.connect(f'{lifecycle_path.as_uri()}?mode=ro', uri=True) as con:
                lifecycle = pd.read_sql_query('SELECT source, lifecycle_state, COUNT(*) AS jobs FROM job_state GROUP BY source, lifecycle_state', con)
                changes = pd.read_sql_query('SELECT source, change_type, COUNT(*) AS versions FROM job_history GROUP BY source, change_type', con)
            st.dataframe(lifecycle, hide_index=True, width='stretch')
            st.dataframe(changes, hide_index=True, width='stretch')
        except (sqlite3.Error, pd.errors.DatabaseError):
            st.info('Run incremental ingestion to initialize lifecycle tracking.')
    else:
        st.info('No lifecycle observations yet. Run the incremental pipeline to begin tracking.')

st.markdown('<div id="opportunity-board" class="section-rule"><span>03 / OPPORTUNITY BOARD</span><span>YOUR NEXT MOVE ↓</span></div>', unsafe_allow_html=True)
listing_header, download_column = st.columns([3, 1])
with listing_header:
    st.subheader('Your next opportunity')
    st.caption(f'{len(filtered):,} matching listings · click a column header to sort')
columns = ['source', 'title', 'company_name', 'location_name', 'raw_location_name', 'remote_label', 'url']
display = filtered[columns].rename(columns={
    'source': 'Source', 'title': 'Job title', 'company_name': 'Company', 'location_name': 'Location', 'raw_location_name': 'Original location',
    'remote_label': 'Work arrangement', 'url': 'Job link'
})
with download_column:
    st.download_button('↓ Export listings', data=display.to_csv(index=False).encode('utf-8'),
                       file_name='filtered_job_listings.csv', mime='text/csv', width='stretch')
st.dataframe(display.style.set_properties(**{'background-color': THEME['surface'], 'color': THEME['text']}), hide_index=True, width='stretch', height=440,
             column_config={'Job title': st.column_config.TextColumn(width='large'),
                            'Job link': st.column_config.LinkColumn('Job link', display_text='View role ↗')})
with st.expander('About this data & methodology'):
    st.write('This dashboard describes the listings captured by this pipeline, rather than the entire European job market. Skills are detected with a keyword dictionary; a mention does not necessarily indicate a required skill. “Not marked remote” does not prove on-site work. Collection timestamps describe ingestion, not the original posting date.')
