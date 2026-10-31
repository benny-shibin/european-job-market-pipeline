from pathlib import Path
import duckdb
import pandas as pd
import plotly.express as px
import streamlit as st

DB_PATH = Path(__file__).resolve().parents[1] / 'data' / 'gold' / 'job_market.duckdb'

st.set_page_config(page_title='European Job Market Intelligence', page_icon='🌍', layout='wide', initial_sidebar_state='expanded')

st.markdown(f"<style>{Path(__file__).with_name('styles.css').read_text()}</style>", unsafe_allow_html=True)

FILTER_KEYS = ['search', 'locations', 'companies', 'skills', 'arrangement']


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
jobs['location_name'] = jobs['location_name'].fillna('Unknown location')
jobs['title'] = jobs['title'].fillna('Untitled role')
jobs['remote_label'] = jobs['remote'].map({True: 'Remote', False: 'Not marked remote'}).fillna('Unknown')
jobs['ingested_at'] = pd.to_datetime(jobs['ingested_at'], utc=True, errors='coerce')

with st.sidebar:
    st.title('🌍 Job Market')
    st.caption('Explore observed job listings')
    st.divider()
    st.subheader('Filters')
    search = st.text_input('Search job titles or companies', placeholder='e.g. Data Engineer', key='search')
    location_filter = st.multiselect('Locations', sorted(jobs['location_name'].unique()), key='locations')
    company_filter = st.multiselect('Companies', sorted(jobs['company_name'].unique()), key='companies')
    skill_filter = st.multiselect('Skills (match any)', sorted(skills['skill_name'].dropna().unique()), key='skills')
    remote_filter = st.selectbox('Work arrangement', ['All', 'Remote', 'Not marked remote', 'Unknown'], key='arrangement')
    st.button('Reset filters', width='stretch', on_click=reset_filters)
    st.divider()
    st.caption('Source: Arbeitnow API · Local DuckDB')
    st.caption('Results describe the collected sample, not the entire European job market.')

filtered = jobs.copy()
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

st.markdown('<div class="eyebrow">EUROPE / HIRING INTELLIGENCE</div>', unsafe_allow_html=True)
st.title('Find the signal in the job market.')
st.markdown('<p class="hero-copy">Explore who is hiring, where opportunities cluster, and which technology skills appear in the listings.</p>', unsafe_allow_html=True)
latest = jobs['ingested_at'].max()
freshness = f"Collected {latest.strftime('%d %b %Y · %H:%M UTC')}" if pd.notna(latest) else 'Collection time unavailable'
st.markdown(f'<div class="status">{freshness} &nbsp; · &nbsp; {len(filtered):,} of {len(jobs):,} listings</div>', unsafe_allow_html=True)

m1, m2, m3, m4 = st.columns(4)
m1.metric('Job postings', f'{len(filtered):,}', help='Listings matching your current filters.')
m2.metric('Hiring companies', f'{filtered["company_name"].replace("Unknown company", pd.NA).nunique():,}')
m3.metric('Locations', f'{filtered["location_name"].replace("Unknown location", pd.NA).nunique():,}')
remote_count = int(filtered['remote'].eq(True).sum())
remote_share = remote_count / len(filtered) if len(filtered) else 0
m4.metric('Marked remote', f'{remote_share:.0%}', help=f'{remote_count:,} postings explicitly marked remote.')
st.write('')

if filtered.empty:
    st.info('No listings match this combination. Broaden your search or reset the sidebar filters.')

COLORS = {'Remote': '#66e0c2', 'Not marked remote': '#6681a4', 'Unknown': '#e9bc73'}


def style_chart(fig, height=410):
    fig.update_layout(
        template='plotly_dark', paper_bgcolor='#121d2b', plot_bgcolor='#121d2b',
        font=dict(family='Inter, system-ui, sans-serif', color='#e2eaf5', size=14),
        height=height, margin=dict(l=16, r=55, t=25, b=45),
        xaxis=dict(gridcolor='#3b4c63', zeroline=False, title_font_size=13, tickfont=dict(color='#d3dfef')),
        yaxis=dict(showgrid=False, zeroline=False, automargin=True, tickfont=dict(color='#e2eaf5', size=14)),
        showlegend=False, hoverlabel=dict(bgcolor='#213349', font=dict(color='#ffffff', size=14)),
        transition=dict(duration=450, easing='cubic-in-out'),
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
    fig = px.bar(counts, x='jobs', y=category, orientation='h', text='jobs',
                 custom_data=['share'], color_discrete_sequence=[color],
                 labels={'jobs': 'Number of job postings', category: ''})
    fig.update_traces(textposition='outside', cliponaxis=False,
                      marker_line_width=0, textfont=dict(color='#f1f5fb', size=14),
                      hovertemplate='<b>%{y}</b><br>%{x:,} postings<br>%{customdata[0]:.1%} of filtered listings<extra></extra>')
    fig.update_xaxes(range=[0, counts['jobs'].max() * 1.22], dtick=1 if counts['jobs'].max() < 8 else None)
    render_chart(fig, key, height)


overview, geography = st.tabs(['Skills & work patterns', 'Companies & locations'])
with overview:
    left, right = st.columns([1.4, 1], gap='large')
    with left:
        st.subheader('Skills in demand')
        st.caption('Top 12 dictionary-matched skills · a posting can mention multiple skills')
        counts = (filtered_skills.drop_duplicates(['source', 'job_id', 'skill_name'])
                  .groupby('skill_name').size().nlargest(12).rename('jobs').reset_index())
        ranking_chart(counts, 'skill_name', '#66e0c2', 'skills_chart', 460)
    with right:
        st.subheader('How work is arranged')
        st.caption('Remote status as reported by the source')
        arrangements = filtered['remote_label'].value_counts().rename_axis('arrangement').reset_index(name='jobs')
        if not arrangements.empty:
            fig = px.pie(arrangements, names='arrangement', values='jobs', hole=.76,
                         color='arrangement', color_discrete_map=COLORS)
            fig.update_traces(textinfo='none', sort=False, marker=dict(line=dict(color='#121d2b', width=5)),
                              hovertemplate='<b>%{label}</b><br>%{value:,} postings · %{percent}<extra></extra>')
            fig.add_annotation(text=f'<b>{remote_share:.0%}</b><br><span style="font-size:13px">marked remote</span>',
                               x=.5, y=.5, showarrow=False, font=dict(size=30, color='#eff5ff'))
            fig = style_chart(fig, 460)
            fig.update_layout(showlegend=True, legend=dict(orientation='h', y=-.08, x=.5, xanchor='center'),
                              margin=dict(l=20, r=20, t=25, b=70))
            st.plotly_chart(fig, width='stretch', key='arrangement_chart', theme=None, config={'displayModeBar': False})
        else:
            st.info('No work arrangement data to display.')
with geography:
    left, right = st.columns(2, gap='large')
    with left:
        st.subheader('Who is hiring')
        st.caption('Top 10 companies by observed job postings')
        counts = (filtered.loc[filtered['company_name'] != 'Unknown company', 'company_name']
                  .value_counts().head(10).rename_axis('company').reset_index(name='jobs'))
        ranking_chart(counts, 'company', '#a99af7', 'companies_chart', 460)
    with right:
        st.subheader('Where opportunities cluster')
        st.caption('Top 10 source-reported locations')
        counts = (filtered.loc[filtered['location_name'] != 'Unknown location', 'location_name']
                  .value_counts().head(10).rename_axis('location').reset_index(name='jobs'))
        ranking_chart(counts, 'location', '#70baff', 'locations_chart', 460)

st.divider()
listing_header, download_column = st.columns([3, 1])
with listing_header:
    st.subheader('Your next opportunity')
    st.caption(f'{len(filtered):,} matching listings · click a column header to sort')
columns = ['title', 'company_name', 'location_name', 'remote_label', 'url']
display = filtered[columns].rename(columns={
    'title': 'Job title', 'company_name': 'Company', 'location_name': 'Location',
    'remote_label': 'Work arrangement', 'url': 'Job link'
})
with download_column:
    st.download_button('↓ Export listings', data=display.to_csv(index=False).encode('utf-8'),
                       file_name='filtered_job_listings.csv', mime='text/csv', width='stretch')
st.dataframe(display, hide_index=True, width='stretch', height=440,
             column_config={'Job title': st.column_config.TextColumn(width='large'),
                            'Job link': st.column_config.LinkColumn('Job link', display_text='View role ↗')})
with st.expander('About this data & methodology'):
    st.write('This dashboard describes the listings captured by this pipeline, rather than the entire European job market. Skills are detected with a keyword dictionary; a mention does not necessarily indicate a required skill. “Not marked remote” does not prove on-site work. Collection timestamps describe ingestion, not the original posting date.')
