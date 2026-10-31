"""Theme palettes and an offline, approximate geographic globe."""
from functools import lru_cache
import json
from pathlib import Path
import re
import unicodedata

import numpy as np
import pandas as pd
import plotly.graph_objects as go

ASSETS = Path(__file__).with_name('assets')
_SOURCE_MTIME_NS = Path(__file__).stat().st_mtime_ns


def palette(light=False):
    if light:
        return dict(bg='#f6f4ee', surface='#fffef9', raised='#ecebe3', text='#242923', muted='#626b60',
                    border='#d9ddd0', grid='#e0e3d8', accent='#426329', blue='#7656c5', purple='#9b58a5',
                    ocean='#e9eddf', land='#788b68', glow='#587c39', hover='#242923', hover_text='#fffef9')
    return dict(bg='#0b0d10', surface='#11151b', raised='#1b222a', text='#f1f2ec', muted='#9da99f',
                border='#2b353a', grid='#263238', accent='#d6ff62', blue='#a89cff', purple='#ffab7a',
                ocean='#151f24', land='#56777b', glow='#d6ff62', hover='#f1f2ec', hover_text='#161c14')


def normalize(value):
    value = unicodedata.normalize('NFKD', str(value).casefold().replace('ß', 'ss'))
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9 ]', ' ', ''.join(c for c in value if not unicodedata.combining(c)))).strip()


@lru_cache(maxsize=1)
def world_data():
    return json.loads((ASSETS / 'world.json').read_text())


@lru_cache(maxsize=1)
def place_index():
    index = {}
    for place in world_data()['places']:
        for alias in [place['name'], *place['aliases']]:
            key = normalize(alias)
            if key:
                index.setdefault(key, []).append(place)
    # Some station parenthetical aliases are country names (for example a
    # research station labelled 'Germany'). Prefer an explicit country alias
    # unless a city's actual name shares it, such as Luxembourg.
    for key, candidates in index.items():
        if any(item['precision'] == 'Country' for item in candidates):
            index[key] = [item for item in candidates if item['precision'] == 'Country' or normalize(item['name']) == key]
    aliases = {'uk': 'united kingdom', 'england': 'united kingdom', 'deutschland': 'germany',
               'munchen': 'munich', 'koln': 'cologne', 'zuerich': 'zurich', 'frankfurt am main': 'frankfurt',
               'wien': 'vienna', 'praha': 'prague', 'den haag': 'the hague', 'brussel': 'brussels',
               'bruxelles': 'brussels', 'nederland': 'netherlands', 'holland': 'netherlands', 'usa': 'united states of america',
               'de': 'germany', 'nl': 'netherlands', 'gb': 'united kingdom', 'u k': 'united kingdom'}
    for alias, name in aliases.items():
        if name in index:
            index[alias] = index[name]
    return index


REMOTE_LABELS = {'remote', 'remote job', 'homeoffice', 'home office', 'remote home based',
                 'home based', 'poste a distance', 'remote unspecified'}
UNKNOWN_LABELS = {'', 'unknown', 'unknown location', 'n a', 'none', 'nan', 'headquarter', 'headquarters'}
REGIONS = {'europe': 'Europe', 'emea': 'EMEA', 'apac': 'APAC', 'latam': 'LATAM',
           'worldwide': 'Worldwide', 'anywhere': 'Worldwide'}
REGION_ALIASES = {'bavaria': 'bayern', 'hesse': 'hessen', 'north holland': 'noord holland',
                  'south holland': 'zuid holland', 'greater london': 'westminster'}


def location_parts(value):
    """Remove work-mode/office wrappers, retaining the original label separately."""
    value = re.sub(r'\b(?:office|hq|headquarters?|hybrid|on[ -]?site|remote)\b', '', str(value), flags=re.I)
    value = re.sub(r'[()]', ',', value)
    return tuple(part for part in (normalize(item) for item in re.split(r'[,;/|•]|\s+[-–—]\s+', value)) if part)


@lru_cache(maxsize=4096)
def resolve_location(value):
    """Resolve a single known place; never invent pins for remote/region/multi-city rows."""
    value = str(value).strip()
    if not value or normalize(value) in {'remote', 'remote job', 'homeoffice', 'anywhere', 'worldwide', 'europe', 'emea', 'unknown location'}:
        return None
    parts = location_parts(value)
    index = place_index()
    matched = []
    for part in parts:
        candidates = index.get(part, [])
        identities = {(p['name'], p['country'], p['precision']) for p in candidates}
        if len(identities) == 1:
            matched.append(candidates[0])
        elif len(identities) > 1:
            # Ambiguous city names need an explicit country in this location string.
            countries = {p['country'] for other in parts for p in index.get(other, []) if p['precision'] == 'Country'}
            eligible = [p for p in candidates if p['country'] in countries]
            if len({(p['name'], p['country'], p['precision']) for p in eligible}) == 1 and eligible:
                matched.append(eligible[0])
    cities = {(p['name'], p['country']): p for p in matched if p['precision'] == 'City'}
    countries = {p['country']: p for p in matched if p['precision'] == 'Country'}
    if len(cities) == 1:
        place = next(iter(cities.values()))
        allowed_region = normalize(place.get('region') or '')
        unknown = [part for part in parts if part and part not in index and REGION_ALIASES.get(part, part) != allowed_region and not re.fullmatch(r'\d{4,6}', part)]
        if not unknown and (not countries or set(countries) == {place['country']}):
            return place
    if not cities and len(countries) == 1 and len(matched) == len(parts):
        return next(iter(countries.values()))
    return None


@lru_cache(maxsize=4096)
def normalize_location_label(value):
    """Canonical UI label; keep geographic precision and source/job identity intact."""
    if value is None or pd.isna(value):
        return 'Unknown location'
    text = re.sub(r'\s+', ' ', str(value)).strip()
    key = normalize(text)
    if key in UNKNOWN_LABELS:
        return 'Unknown location'
    if key in REMOTE_LABELS:
        return 'Remote / unspecified'
    if key in REGIONS:
        return REGIONS[key]
    place = resolve_location(text)
    if place:
        return f"{place['name']}, {place['country']}" if place['precision'] == 'City' else place['name']
    parts = location_parts(text)
    if not parts:
        return 'Unknown location'
    # Normalize/reorder a list only when every member is a known country or
    # broad region. Do not reduce an unresolved city to its country.
    labels = []
    for part in parts:
        if part in REGIONS:
            labels.append(REGIONS[part])
            continue
        candidates = place_index().get(part, [])
        countries = {item['name'] for item in candidates if item['precision'] == 'Country'}
        if len(countries) == 1 and all(item['precision'] == 'Country' for item in candidates):
            labels.append(next(iter(countries)))
        else:
            break
    else:
        return ' / '.join(sorted(set(labels)))
    # Case, punctuation and duplicate components still normalize for smaller
    # places outside the gazetteer; leave their geography unresolved. Put an
    # explicit country last so 'UK (Reading)' and 'Reading, UK' group together.
    display, country_labels = [], []
    for part in parts:
        candidates = place_index().get(part, [])
        country_names = {item['name'] for item in candidates if item['precision'] == 'Country'}
        if len(country_names) == 1 and all(item['precision'] == 'Country' for item in candidates):
            country_labels.append(next(iter(country_names)))
            continue
        identities = {(item['name'], item['country'], item['precision']) for item in candidates}
        name = candidates[0]['name'] if len(identities) == 1 else REGIONS.get(part, part.title())
        if name not in display:
            display.append(name)
    return ', '.join([*display, *sorted(set(country_labels))])


def normalize_job_locations(jobs):
    result = jobs.copy()
    if 'raw_location_name' not in result.columns:
        result['raw_location_name'] = result['location_name']
    result['location_name'] = result['raw_location_name'].map(normalize_location_label)
    result['country_name'] = result['raw_location_name'].map(location_country)
    return result


@lru_cache(maxsize=4096)
def location_country(value):
    """Assign at most one country; broad or conflicting scopes stay unassigned."""
    if value is None or pd.isna(value):
        return None
    place = resolve_location(value)
    if place:
        return place['country']
    parts = location_parts(value)
    if not parts or any(part in REGIONS or part in REMOTE_LABELS for part in parts):
        return None
    explicit, city_countries = set(), set()
    all_known = True
    for part in parts:
        candidates = place_index().get(part, [])
        explicit.update(item['country'] for item in candidates if item['precision'] == 'Country')
        city_countries.update(item['country'] for item in candidates if item['precision'] == 'City')
        all_known = all_known and bool(candidates)
    if len(explicit) == 1 and (not city_countries or city_countries <= explicit):
        return next(iter(explicit))
    if not explicit and all_known and len(city_countries) == 1:
        return next(iter(city_countries))
    return None


def country_statistics(jobs):
    unique = jobs.drop_duplicates(['source', 'job_id']).copy()
    raw = unique['raw_location_name'] if 'raw_location_name' in unique else unique['location_name']
    unique['country'] = raw.map(location_country)
    unique['marked_remote'] = unique['remote'].eq(True).fillna(False).astype(int)
    if 'company_name' not in unique:
        unique['company_name'] = None
    unique['company_name'] = unique['company_name'].replace('Unknown company', None)
    assigned = unique.dropna(subset=['country'])
    summary = assigned.groupby('country', as_index=False).agg(
        jobs=('job_id', 'size'), remote_jobs=('marked_remote', 'sum'), companies=('company_name', 'nunique'))
    summary['remote_share'] = summary['remote_jobs'].div(summary['jobs']).mul(100)
    summary['share'] = summary['jobs'].div(len(unique) or 1).mul(100)
    return summary.sort_values(['jobs', 'country'], ascending=[False, True]), len(unique) - len(assigned)


def map_jobs(jobs):
    rows = []
    for row in jobs.drop_duplicates(['source', 'job_id']).itertuples():
        place = resolve_location(getattr(row, 'raw_location_name', row.location_name))
        if place:
            rows.append(dict(source=row.source, job_id=row.job_id, place=place['name'], country=place['country'],
                             latitude=place['lat'], longitude=place['lon'], precision=place['precision'],
                             remote=bool(row.remote) if pd.notna(row.remote) else False))
    columns = ['place', 'country', 'latitude', 'longitude', 'precision', 'jobs', 'remote_jobs']
    if not rows:
        return pd.DataFrame(columns=columns), 0
    points = pd.DataFrame(rows).groupby(columns[:5], as_index=False).agg(jobs=('job_id', 'size'), remote_jobs=('remote', 'sum'))
    return points, len(rows)


def xyz(lon, lat, radius=1):
    lon, lat = np.radians(lon), np.radians(lat)
    return radius * np.cos(lat) * np.cos(lon), radius * np.cos(lat) * np.sin(lon), radius * np.sin(lat)


def globe_figure(points, colors, show_country_labels=True, label_detail='Major countries'):
    figure = go.Figure()
    lon, lat = np.meshgrid(np.linspace(-180, 180, 100), np.linspace(-90, 90, 65))
    x, y, z = xyz(lon, lat)
    figure.add_trace(go.Surface(x=x, y=y, z=z, surfacecolor=np.zeros_like(x),
        colorscale=[[0, colors['ocean']], [1, colors['ocean']]], showscale=False, hoverinfo='skip',
        lighting=dict(ambient=.85, diffuse=.7, specular=.25, roughness=.8), name='Earth'))
    for kind in ['grid', 'land']:
        paths = []
        if kind == 'land':
            paths = world_data()['boundaries']
        else:
            paths = [[(lon, lat) for lon in np.linspace(-180, 180, 120)] for lat in range(-60, 90, 30)]
            paths += [[(lon, lat) for lat in np.linspace(-90, 90, 70)] for lon in range(-180, 180, 30)]
        xx, yy, zz = [], [], []
        for path in paths:
            coords = np.asarray(path)
            cx, cy, cz = xyz(coords[:, 0], coords[:, 1], 1.004 if kind == 'grid' else 1.01)
            xx.extend([*cx, None]); yy.extend([*cy, None]); zz.extend([*cz, None])
        figure.add_trace(go.Scatter3d(x=xx, y=yy, z=zz, mode='lines',
            line=dict(color=colors['grid'] if kind == 'grid' else colors['land'], width=1 if kind == 'grid' else 2),
            opacity=.55 if kind == 'grid' else .8, hoverinfo='skip', showlegend=False, name=kind))
    angle = np.linspace(0, 2 * np.pi, 220)
    figure.add_trace(go.Scatter3d(x=1.18 * np.cos(angle), y=1.18 * np.sin(angle) * .85,
        z=1.18 * np.sin(angle) * .52, mode='lines', line=dict(color=colors['blue'], width=2),
        opacity=.28, hoverinfo='skip', showlegend=False, name='Orbit'))
    if not points.empty:
        px, py, pz = xyz(points['longitude'].to_numpy(), points['latitude'].to_numpy(), 1.025)
        size = np.clip(4 + np.sqrt(points['jobs'].to_numpy()) * .8, 5, 14)
        custom = points[['place', 'country', 'jobs', 'precision', 'remote_jobs']].to_numpy()
        figure.add_trace(go.Scatter3d(x=px, y=py, z=pz, mode='markers',
            marker=dict(size=size + 7, color=colors['glow'], opacity=.14), hoverinfo='skip', showlegend=False, name='Signal halo'))
        figure.add_trace(go.Scatter3d(x=px, y=py, z=pz, mode='markers', customdata=custom,
            marker=dict(size=size, color=points['jobs'], colorscale=[[0, colors['accent']], [1, colors['blue']]],
                        line=dict(width=1, color=colors['text']), opacity=1,
                        symbol=['diamond' if value == 'Country' else 'circle' for value in points['precision']]), name='Job locations', showlegend=False,
            hovertemplate='<b>%{customdata[0]}</b> · %{customdata[1]}<br>%{customdata[2]} sampled postings<br>%{customdata[4]} marked remote<br>%{customdata[3]}-level approximation<extra></extra>'))
    if show_country_labels:
        represented = set(points['country']) if not points.empty else set()
        if label_detail == 'Major countries':
            largest = set(points.groupby('country')['jobs'].sum().nlargest(8).index) if not points.empty else set()
            names = largest | {'France', 'Germany', 'United Kingdom', 'Italy', 'Spain', 'Norway', 'Finland'}
        else:
            names = represented | {place['name'] for place in world_data()['places'] if place['precision'] == 'Country'
                                   and -15 <= place['lon'] <= 40 and 35 <= place['lat'] <= 65}
        countries = [place for place in world_data()['places'] if place['precision'] == 'Country' and place['name'] in names]
        positions = {'United Kingdom': 'top left', 'Ireland': 'bottom left', 'France': 'bottom left',
                     'Germany': 'top right', 'Netherlands': 'middle left', 'Switzerland': 'bottom right',
                     'Italy': 'bottom right', 'Spain': 'bottom left', 'Norway': 'top left', 'Sweden': 'top right'}
        lx, ly, lz = xyz(np.array([place['lon'] for place in countries]), np.array([place['lat'] for place in countries]), 1.055)
        figure.add_trace(go.Scatter3d(x=lx, y=ly, z=lz, mode='text',
            text=[place['name'] for place in countries],
            textposition=[positions.get(place['name'], 'top center') for place in countries],
            textfont=dict(size=11, color=colors['text'], family='SFMono-Regular, Consolas, monospace'), hoverinfo='skip', showlegend=False, name='Country labels'))
    axis = dict(visible=False, range=[-1.1, 1.1], showbackground=False)
    figure.update_layout(height=610, margin=dict(l=0, r=0, t=0, b=0), paper_bgcolor=colors['surface'],
        font=dict(color=colors['text']), showlegend=False, uirevision='job-globe-europe-close-v2',
        scene=dict(xaxis=axis, yaxis=axis, zaxis=axis, bgcolor=colors['surface'], aspectmode='cube',
                   camera=dict(eye=dict(x=.98, y=.12, z=1.02), up=dict(x=0,y=0,z=1), projection=dict(type='perspective'))))
    return figure
