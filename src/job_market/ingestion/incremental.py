
import hashlib
import json
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from src.job_market.storage.files import atomic_json
from src.job_market.quality.reports import publish_report
from src.job_market.ingestion.sources import endpoint, parameters, parse_payload, validate_job

API_URL = "https://www.arbeitnow.com/api/job-board-api"
DB_PATH = Path("data/state/jobs.sqlite")
BRONZE_PATH = Path("data/bronze/arbeitnow")


def content_hash(job):
    # Ignore collection metadata; hash the source job itself.
    canonical = json.dumps(
        {key: value for key, value in job.items() if key not in {"views", "clicks", "last_seen", "fetched_at"}},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()



def initialize_database(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS job_state (
            source TEXT NOT NULL,
            job_id TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (source, job_id)
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS job_history (
            source TEXT NOT NULL,
            job_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            content_hash TEXT NOT NULL,
            change_type TEXT NOT NULL,
            detected_at TEXT NOT NULL,
            job_json TEXT NOT NULL,
            PRIMARY KEY (source, job_id, version)
        )
    """)

    columns = {row[1] for row in con.execute("PRAGMA table_info(job_state)")}
    if "lifecycle_state" not in columns:
        con.execute("ALTER TABLE job_state ADD COLUMN lifecycle_state TEXT NOT NULL DEFAULT 'observed'")
    con.executescript("""
        CREATE TABLE IF NOT EXISTS ingestion_runs (
            run_id TEXT PRIMARY KEY, source TEXT NOT NULL, started_at TEXT NOT NULL,
            finished_at TEXT, status TEXT NOT NULL, max_pages INTEGER NOT NULL,
            pages INTEGER NOT NULL DEFAULT 0, pagination_complete INTEGER NOT NULL DEFAULT 0,
            counts_json TEXT NOT NULL DEFAULT '{}', error TEXT
        );
        CREATE TABLE IF NOT EXISTS ingestion_pages (
            run_id TEXT NOT NULL, page INTEGER NOT NULL, path TEXT NOT NULL,
            PRIMARY KEY (run_id, page)
        );
        CREATE TABLE IF NOT EXISTS run_changes (
            run_id TEXT NOT NULL, source TEXT NOT NULL, job_id TEXT NOT NULL,
            version INTEGER NOT NULL, change_type TEXT NOT NULL,
            PRIMARY KEY (run_id, source, job_id, version)
        );
        CREATE TABLE IF NOT EXISTS run_observations (
            run_id TEXT NOT NULL, source TEXT NOT NULL, job_id TEXT NOT NULL,
            PRIMARY KEY (run_id, source, job_id)
        );
        CREATE TABLE IF NOT EXISTS lifecycle_events (
            run_id TEXT NOT NULL, source TEXT NOT NULL, job_id TEXT NOT NULL,
            state TEXT NOT NULL, detected_at TEXT NOT NULL,
            PRIMARY KEY (run_id, source, job_id)
        );
    """)
    page_columns = {row[1] for row in con.execute('PRAGMA table_info(ingestion_pages)')}
    if 'counts_json' not in page_columns:
        con.execute("ALTER TABLE ingestion_pages ADD COLUMN counts_json TEXT NOT NULL DEFAULT '{}'")
    con.commit()



def classify_job(con, job, timestamp, source="arbeitnow"):
    validate_job(job)
    job_id = str(job.get("slug") or job.get("url") or "")

    if not job_id:
        return "invalid"

    new_hash = content_hash(job)

    existing = con.execute("""
        SELECT content_hash, version
        FROM job_state
        WHERE source = ? AND job_id = ?
    """, (source, job_id)).fetchone()

    if existing is None:
        con.execute("""
            INSERT INTO job_state
            (source, job_id, content_hash,
             first_seen, last_seen, version)
            VALUES (?, ?, ?, ?, ?, 1)
        """, (
            source, job_id, new_hash,
            timestamp, timestamp
        ))

        status = "new"
        version = 1

    else:
        old_hash, version = existing

        if old_hash != new_hash:
            version += 1

            con.execute("""
                UPDATE job_state
                SET content_hash = ?,
                    last_seen = ?,
                    lifecycle_state = 'observed',
                    version = ?
                WHERE source = ? AND job_id = ?
            """, (
                new_hash, timestamp, version,
                source, job_id
            ))

            status = "updated"

        else:
            con.execute("""
                UPDATE job_state
                SET last_seen = ?, lifecycle_state = 'observed'
                WHERE source = ? AND job_id = ?
            """, (timestamp, source, job_id))

            return "unchanged"

    # Preserve each new or changed version.
    con.execute("""
        INSERT INTO job_history
        (source, job_id, version, content_hash,
         change_type, detected_at, job_json)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        source,
        job_id,
        version,
        new_hash,
        status,
        timestamp,
        json.dumps(job, ensure_ascii=False)
    ))

    return status


def _apply_page(con, path):
    raw = json.loads(path.read_text(encoding="utf-8"))
    run_id, source, page = raw['run_id'], raw['source'], raw['page']
    if con.execute('SELECT 1 FROM ingestion_pages WHERE run_id=? AND page=?', (run_id, page)).fetchone():
        return {name: 0 for name in ['new', 'updated', 'unchanged', 'invalid']}
    jobs, _ = parse_payload(source, raw['payload'])
    stats = {name: 0 for name in ['new', 'updated', 'unchanged', 'invalid']}
    with con:
        for job in jobs:
            job_id = str(job.get('slug') or job.get('url') or '')
            previous = con.execute('SELECT lifecycle_state FROM job_state WHERE source=? AND job_id=?', (source, job_id)).fetchone()
            status = classify_job(con, job, raw['ingested_at'], source)
            if status != 'invalid' and (previous is None or previous[0] != 'observed'):
                con.execute('INSERT OR REPLACE INTO lifecycle_events VALUES (?, ?, ?, ?, ?)', (run_id, source, job_id, 'observed', raw['ingested_at']))
            stats[status] += 1
            job_id = str(job.get('slug') or job.get('url') or '')
            if status != 'invalid':
                con.execute('INSERT OR IGNORE INTO run_observations VALUES (?, ?, ?)', (run_id, source, job_id))
            if status in ('new', 'updated'):
                version = con.execute('SELECT version FROM job_state WHERE source=? AND job_id=?', (source, job_id)).fetchone()[0]
                con.execute('INSERT OR IGNORE INTO run_changes VALUES (?, ?, ?, ?, ?)', (run_id, source, job_id, version, status))
        con.execute('INSERT INTO ingestion_pages VALUES (?, ?, ?, ?)', (run_id, page, str(path), json.dumps(stats)))
        totals = {key: 0 for key in stats}
        for (counts,) in con.execute('SELECT counts_json FROM ingestion_pages WHERE run_id=?', (run_id,)):
            for key, value in json.loads(counts).items():
                totals[key] += value
        con.execute('UPDATE ingestion_runs SET pages=(SELECT COUNT(*) FROM ingestion_pages WHERE run_id=?), counts_json=? WHERE run_id=?',
                    (run_id, json.dumps(totals), run_id))
    return stats


def recover_pages(con, bronze_root):
    # Check paths against the ledger before parsing: committed Bronze and legacy
    # runs are not reread on every poll. Only runs owned by this collector replay.
    known = {row[0] for row in con.execute('SELECT run_id FROM ingestion_runs')}
    committed = {str(Path(row[0]).resolve()) for row in con.execute('SELECT path FROM ingestion_pages')}
    affected = {row[0] for row in con.execute("SELECT run_id FROM ingestion_runs WHERE status='running'")}
    for path in sorted(bronze_root.rglob('page_*.json')):
        run_id = path.parent.name.removeprefix('run=')
        if run_id not in known or str(path.resolve()) in committed:
            continue
        _apply_page(con, path)
        affected.add(run_id)
    con.execute("UPDATE ingestion_runs SET status='interrupted' WHERE status='running'")
    con.commit()
    for run_id in known:
        row = con.execute('SELECT path FROM ingestion_pages WHERE run_id=? LIMIT 1', (run_id,)).fetchone()
        if row and (run_id in affected or not Path(row[0]).with_name('manifest.json').exists()):
            _finish_manifest(con, Path(row[0]).parent, run_id)


def _finish_manifest(con, output_dir, run_id):
    con.row_factory = sqlite3.Row
    row = dict(con.execute('SELECT * FROM ingestion_runs WHERE run_id=?', (run_id,)).fetchone())
    con.row_factory = None
    row['counts'] = json.loads(row.pop('counts_json'))
    row['fetched_pages'] = [p[0] for p in con.execute('SELECT page FROM ingestion_pages WHERE run_id=? ORDER BY page', (run_id,))]
    row['changes'] = [dict(zip(['source', 'job_id', 'version', 'change_type'], item)) for item in con.execute(
        'SELECT source, job_id, version, change_type FROM run_changes WHERE run_id=? ORDER BY source, job_id, version', (run_id,))]
    atomic_json(output_dir / 'manifest.json', row)


def run_incremental(max_pages=2, source='arbeitnow'):
    if max_pages < 1:
        raise ValueError('max_pages must be positive')
    run_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    timestamp = now.isoformat()
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    bronze_root = BRONZE_PATH if source == 'arbeitnow' else BRONZE_PATH.parent / source
    output_dir = bronze_root / f'date={now:%Y-%m-%d}' / f'run={run_id}'
    output_dir.mkdir(parents=True, exist_ok=True)
    stats = {name: 0 for name in ['new', 'updated', 'unchanged', 'invalid']}
    with sqlite3.connect(DB_PATH) as con:
        initialize_database(con)
        recover_pages(con, bronze_root)
        # Jobicy permits a new synchronization pass at most hourly.
        if source == 'jobicy':
            recent = con.execute("SELECT started_at FROM ingestion_runs WHERE source=? ORDER BY started_at DESC LIMIT 1", (source,)).fetchone()
            if recent and (now - datetime.fromisoformat(recent[0])).total_seconds() < 3600:
                raise RuntimeError('Jobicy polling is limited to once per hour; use stored Bronze for retries')
        con.execute('INSERT INTO ingestion_runs (run_id, source, started_at, status, max_pages) VALUES (?, ?, ?, ?, ?)',
                    (run_id, source, timestamp, 'running', max_pages))
        con.commit()
        _finish_manifest(con, output_dir, run_id)
        complete = False
        cursor = None
        try:
            with requests.Session() as session:
                retries = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504],
                                allowed_methods=['GET'], respect_retry_after_header=True)
                session.mount('https://', HTTPAdapter(max_retries=retries))
                for page in range(1, max_pages + 1):
                    response = session.get(endpoint(source), params=parameters(source, page, cursor),
                                           timeout=30, headers={'User-Agent': 'JobMarketPortfolio/0.2'})
                    response.raise_for_status()
                    payload = response.json()
                    jobs, cursor = parse_payload(source, payload)
                    if source == 'arbeitnow' and 'next' not in payload.get('links', {}):
                        raise ValueError('Missing authoritative pagination metadata')
                    if source == 'jobicy' and ('nextCursor' not in payload or not isinstance(payload.get('hasMore'), bool)):
                        raise ValueError('Missing authoritative pagination metadata')
                    for job in jobs:
                        validate_job(job)
                        if not (job.get('slug') or job.get('url')):
                            raise ValueError('Missing job identity in API response')
                    path = output_dir / f'page_{page}.json'
                    atomic_json(path, dict(source=source, run_id=run_id, ingested_at=timestamp, page=page, payload=payload))
                    changes = _apply_page(con, path)
                    for key in stats:
                        stats[key] += changes[key]
                    con.execute('UPDATE ingestion_runs SET pages=?, counts_json=? WHERE run_id=?', (page, json.dumps(stats), run_id))
                    con.commit()
                    _finish_manifest(con, output_dir, run_id)
                    print(f'Page {page}: {len(jobs)} records processed')
                    if not cursor:
                        complete = True
                        break
                    if page < max_pages:
                        time.sleep(2)
            publish_report('ingestion-' + source, dict(schema_valid=True, identities_valid=stats['invalid'] == 0),
                           details=dict(source_run_id=run_id, counts=stats, pagination_complete=complete))
            with con:
                # Complete source traversal is needed to compare visibility.
                # Jobicy has a rolling seven-day feed: absence still is not closure.
                if complete:
                    absent = con.execute("""SELECT job_id FROM job_state s WHERE source=? AND lifecycle_state='observed'
                        AND NOT EXISTS (SELECT 1 FROM run_observations o WHERE o.run_id=? AND o.source=s.source AND o.job_id=s.job_id)""", (source, run_id)).fetchall()
                    for (job_id,) in absent:
                        con.execute("UPDATE job_state SET lifecycle_state='not_observed' WHERE source=? AND job_id=?", (source, job_id))
                        con.execute('INSERT INTO lifecycle_events VALUES (?, ?, ?, ?, ?)', (run_id, source, job_id, 'not_observed', timestamp))
                con.execute("UPDATE ingestion_runs SET status='complete', pagination_complete=?, finished_at=? WHERE run_id=?",
                            (int(complete), datetime.now(timezone.utc).isoformat(), run_id))
        except BaseException as exc:
            try:
                publish_report('ingestion-' + source, dict(fetch_and_validation_succeeded=False),
                               details=dict(source_run_id=run_id, error=str(exc)))
            except ValueError:
                pass
            with con:
                con.execute("UPDATE ingestion_runs SET status='failed', error=?, finished_at=? WHERE run_id=?",
                            (str(exc), datetime.now(timezone.utc).isoformat(), run_id))
            raise
        finally:
            _finish_manifest(con, output_dir, run_id)
    print(json.dumps(dict(event='stage_metrics', source=source, **stats)))
    print('INCREMENTAL INGESTION REPORT', json.dumps(stats))
    return stats


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', choices=['arbeitnow', 'jobicy'], default='arbeitnow')
    parser.add_argument('--max-pages', type=int, default=2)
    parser.add_argument('--recover-only', action='store_true')
    args = parser.parse_args()
    if args.recover_only:
        if DB_PATH.exists():
            with sqlite3.connect(DB_PATH) as con:
                initialize_database(con)
                recover_pages(con, BRONZE_PATH.parent)
    else:
        run_incremental(max_pages=args.max_pages, source=args.source)
