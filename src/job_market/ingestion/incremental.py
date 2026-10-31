
import hashlib
import json
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests

API_URL = "https://www.arbeitnow.com/api/job-board-api"
DB_PATH = Path("data/state/jobs.sqlite")
BRONZE_PATH = Path("data/bronze/arbeitnow")


def content_hash(job):
    # Ignore collection metadata; hash the source job itself.
    canonical = json.dumps(
        job,
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

    con.commit()



def classify_job(con, job, timestamp):
    job_id = str(job.get("slug") or job.get("url") or "")

    if not job_id:
        return "invalid"

    source = "arbeitnow"
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
                SET last_seen = ?
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


def run_incremental(max_pages=2):
    run_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    timestamp = now.isoformat()

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    output_dir = (
        BRONZE_PATH
        / f"date={now:%Y-%m-%d}"
        / f"run={run_id}"
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    stats = {
        "new": 0,
        "updated": 0,
        "unchanged": 0,
        "invalid": 0,
    }

    with sqlite3.connect(DB_PATH) as con:
        initialize_database(con)

        with requests.Session() as session:
            for page in range(1, max_pages + 1):
                response = session.get(
                    API_URL,
                    params={"page": page},
                    timeout=30,
                    headers={
                        "User-Agent": "JobMarketPortfolio/0.1"
                    },
                )
                response.raise_for_status()

                payload = response.json()
                jobs = payload.get("data", [])

                if not isinstance(jobs, list):
                    raise ValueError("Unexpected API response")

                # Preserve the raw response before updating state.
                record = {
                    "source": "arbeitnow",
                    "run_id": run_id,
                    "ingested_at": timestamp,
                    "page": page,
                    "payload": payload,
                }

                output_file = output_dir / f"page_{page}.json"
                temporary_file = output_dir / f"page_{page}.tmp"

                temporary_file.write_text(
                    json.dumps(record, ensure_ascii=False),
                    encoding="utf-8",
                )
                temporary_file.replace(output_file)

                # Commit the state only after the raw page is saved.
                for job in jobs:
                    status = classify_job(con, job, timestamp)
                    stats[status] += 1

                con.commit()

                print(
                    f"Page {page}: "
                    f"{len(jobs)} records processed"
                )

                if not jobs or not payload.get("links", {}).get("next"):
                    break

                if page < max_pages:
                    time.sleep(2)

    print("\nINCREMENTAL INGESTION REPORT")
    print("-" * 40)
    for key, value in stats.items():
        print(f"{key.capitalize()}: {value}")

    return stats


if __name__ == "__main__":
    run_incremental()
