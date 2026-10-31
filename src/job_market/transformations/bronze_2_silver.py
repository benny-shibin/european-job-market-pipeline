"""Replay new Bronze pages, upsert changed jobs, and export only dirty sources."""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import polars as pl

from src.job_market.ingestion.incremental import content_hash
from src.job_market.ingestion.sources import parse_payload, validate_job
from src.job_market.storage.files import atomic_parquet

BRONZE_DIR = Path('data/bronze/arbeitnow')
SILVER_DIR = Path('data/silver/arbeitnow')
SCHEMA = dict(source=pl.String, job_id=pl.String, title=pl.String, company=pl.String,
              location=pl.String, remote=pl.Boolean, url=pl.String,
              ingested_at=pl.Datetime('us', 'UTC'), description=pl.String,
              created_at=pl.Int64, tags=pl.List(pl.String), content_hash=pl.String,
              version=pl.Int64)


def frame(rows):
    values = []
    for row in rows:
        row = dict(row)
        row['ingested_at'] = datetime.fromisoformat(row['ingested_at'].replace('Z', '+00:00'))
        values.append(row)
    return pl.DataFrame(values, schema=SCHEMA)


def build_silver():
    SILVER_DIR.parent.mkdir(parents=True, exist_ok=True)
    changed = []
    pages_read = 0
    with sqlite3.connect(SILVER_DIR.parent / 'processing.sqlite') as con:
        con.executescript('''
            CREATE TABLE IF NOT EXISTS jobs (
                source TEXT, job_id TEXT, content_hash TEXT, observed_at TEXT,
                row_json TEXT, PRIMARY KEY (source, job_id)
            );
            CREATE TABLE IF NOT EXISTS pages (path TEXT PRIMARY KEY, signature TEXT);
            CREATE TABLE IF NOT EXISTS pending_exports (source TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS staging_rows (source TEXT, job_id TEXT, row_json TEXT,
                PRIMARY KEY(source, job_id));
        ''')
        # Each immutable page is processed once. Legacy single-source Bronze is supported.
        paths = sorted(BRONZE_DIR.parent.glob('*/date=*/run=*/page_*.json'))
        for path in paths:
            stat = path.stat()
            signature = f'{stat.st_mtime_ns}:{stat.st_size}'
            if con.execute('SELECT 1 FROM pages WHERE path=? AND signature=?', (str(path), signature)).fetchone():
                continue
            raw = json.loads(path.read_text(encoding='utf-8'))
            timestamp = datetime.fromisoformat(raw['ingested_at'].replace('Z', '+00:00'))
            if timestamp.tzinfo is None:
                raise ValueError('Bronze ingested_at must include a timezone')
            timestamp = timestamp.astimezone(timezone.utc).isoformat()
            jobs, _ = parse_payload(raw['source'], raw['payload'])
            page_changes = []
            with con:
                for job in jobs:
                    validate_job(job)
                    job_id = str(job.get('slug') or job.get('url') or '')
                    if not job_id:
                        raise ValueError(f'Missing job identity in {path}')
                    source = raw['source']
                    digest = content_hash(job)
                    old = con.execute('SELECT content_hash, observed_at, row_json FROM jobs WHERE source=? AND job_id=?', (source, job_id)).fetchone()
                    if old and (timestamp < old[1] or digest == old[0]):
                        continue
                    version = json.loads(old[2]).get('version', 1) + 1 if old else 1
                    row = dict(source=source, job_id=job_id, title=job.get('title'), company=job.get('company_name'),
                               location=job.get('location'), remote=job.get('remote'), url=job.get('url'),
                               ingested_at=timestamp, description=job.get('description'), created_at=job.get('created_at'),
                               tags=[str(tag) for tag in (job.get('tags') or [])], content_hash=digest, version=version)
                    con.execute('INSERT OR REPLACE INTO jobs VALUES (?, ?, ?, ?, ?)', (source, job_id, digest, timestamp, json.dumps(row)))
                    con.execute('INSERT OR IGNORE INTO pending_exports VALUES (?)', (source,))
                    con.execute('INSERT OR REPLACE INTO staging_rows VALUES (?, ?, ?)', (source, job_id, json.dumps(row)))
                    page_changes.append(row)
                con.execute('INSERT OR REPLACE INTO pages VALUES (?, ?)', (str(path), signature))
            changed.extend(page_changes)
            pages_read += 1
        # Re-export after a crash even when all pages have already committed.
        for (source,) in con.execute('SELECT DISTINCT source FROM jobs').fetchall():
            if not (SILVER_DIR.parent / source / 'jobs.parquet').exists():
                con.execute('INSERT OR IGNORE INTO pending_exports VALUES (?)', (source,))
        con.commit()
        for (source,) in con.execute('SELECT source FROM pending_exports').fetchall():
            rows = [json.loads(row[0]) for row in con.execute('SELECT row_json FROM jobs WHERE source=? ORDER BY job_id', (source,))]
            atomic_parquet(frame(rows), SILVER_DIR.parent / source / 'jobs.parquet')
            stage = [json.loads(row[0]) for row in con.execute('SELECT row_json FROM staging_rows WHERE source=? ORDER BY job_id', (source,))]
            atomic_parquet(frame(stage), SILVER_DIR.parent / source / 'staging.parquet')
            con.execute('DELETE FROM pending_exports WHERE source=?', (source,))
            con.execute('DELETE FROM staging_rows WHERE source=?', (source,))
            con.commit()
    print(json.dumps(dict(event='stage_metrics', pages_read=pages_read, changed_jobs=len(changed))))
    print(f'Silver: {pages_read} new pages read; {len(changed)} changed records upserted')
    return dict(pages_read=pages_read, changed_jobs=len(changed))


if __name__ == '__main__':
    build_silver()
