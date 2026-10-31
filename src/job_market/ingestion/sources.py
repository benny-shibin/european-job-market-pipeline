"""Source adapters preserve native IDs and keep the original response in Bronze."""
from datetime import datetime, timezone
import html


SOURCES = ('arbeitnow', 'jobicy')


def endpoint(source):
    return {'arbeitnow': 'https://www.arbeitnow.com/api/job-board-api',
            'jobicy': 'https://jobicy.com/api/jobs'}[source]


def parameters(source, page, cursor=None):
    if source == 'arbeitnow':
        return {'page': page}
    return {'count': 100, 'geo': 'europe', **({'cursor': cursor} if cursor else {})}


def parse_payload(source, payload):
    if not isinstance(payload, dict):
        raise ValueError('Expected an API object')
    field = 'data' if source == 'arbeitnow' else 'jobs'
    jobs = payload.get(field)
    if not isinstance(jobs, list) or any(not isinstance(job, dict) for job in jobs):
        raise ValueError(f'Unexpected API response: {field} must be a list of objects')
    if source == 'arbeitnow':
        links = payload.get('links', {})
        if not isinstance(links, dict):
            raise ValueError('Invalid pagination links')
        next_page = links.get('next')
        if next_page is not None and (not isinstance(next_page, str) or not next_page.strip()):
            raise ValueError('Invalid next-page link')
        return jobs, next_page
    if payload.get('success') is False:
        raise ValueError('Jobicy reported an unsuccessful request')
    cursor = payload.get('nextCursor')
    if cursor is not None and (not isinstance(cursor, str) or not cursor.strip()):
        raise ValueError('Invalid Jobicy cursor')
    if payload.get('hasMore') is False and cursor:
        raise ValueError('Jobicy pagination flags disagree')
    if payload.get('hasMore') and not cursor:
        raise ValueError('Jobicy hasMore requires nextCursor')
    result = []
    for job in jobs:
        if isinstance(job.get('id'), bool) or not isinstance(job.get('id'), (str, int)) or not str(job.get('id')).strip():
            raise ValueError('Jobicy job is missing its native ID')
        published = job.get('pubDate')
        created = None
        if published:
            date = datetime.fromisoformat(published.replace('Z', '+00:00'))
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            created = int(date.timestamp())
        result.append(dict(slug=str(job['id']), title=html.unescape(job.get('jobTitle') or ''),
                           company_name=html.unescape(job.get('companyName') or ''),
                           location=job.get('jobGeo'), remote=True, url=job.get('url'),
                           description=job.get('jobDescription'), created_at=created,
                           tags=job.get('jobIndustry') or []))
    return result, cursor


def validate_job(job):
    for field in ['slug', 'url', 'title', 'company_name', 'description', 'location']:
        if job.get(field) is not None and not isinstance(job[field], str):
            raise ValueError(f'{field} must be text or null')
    if job.get('remote') is not None and not isinstance(job['remote'], bool):
        raise ValueError('remote must be boolean or null')
    if not isinstance(job.get('tags', []), list):
        raise ValueError('tags must be a list')
    created = job.get('created_at')
    if created is not None:
        if isinstance(created, bool) or not isinstance(created, (int, float)):
            raise ValueError('created_at must be a Unix timestamp')
        datetime.fromtimestamp(created, timezone.utc)
