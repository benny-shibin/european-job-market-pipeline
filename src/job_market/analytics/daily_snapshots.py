
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import duckdb


GOLD_DB = Path("data/gold/job_market.duckdb")
SNAPSHOT_DB = Path("data/state/snapshots.sqlite")


def create_daily_snapshot(snapshot_date=None):
    if snapshot_date is None:
        snapshot_date = datetime.now(timezone.utc).date().isoformat()

    if not GOLD_DB.exists():
        raise FileNotFoundError(f"Gold database not found: {GOLD_DB}")

    SNAPSHOT_DB.parent.mkdir(parents=True, exist_ok=True)

    with duckdb.connect(str(GOLD_DB), read_only=True) as gold:
        # Use the job-level source rows rather than joining
        # dimensions, which could multiply the job count.
        total_jobs = gold.execute("""
            SELECT COUNT(*)
            FROM fact_job_postings
        """).fetchone()[0]

        total_companies = gold.execute("""
            SELECT COUNT(*)
            FROM dim_companies
        """).fetchone()[0]

        total_locations = gold.execute("""
            SELECT COUNT(*)
            FROM dim_locations
        """).fetchone()[0]

        # Discover existing columns before assuming a remote flag.
        columns = {
            row[1]
            for row in gold.execute(
                "PRAGMA table_info('fact_job_postings')"
            ).fetchall()
        }

        if "remote" in columns:
            remote_jobs = gold.execute("""
                SELECT COUNT(*)
                FROM fact_job_postings
                WHERE remote = TRUE
            """).fetchone()[0]
        elif "is_remote" in columns:
            remote_jobs = gold.execute("""
                SELECT COUNT(*)
                FROM fact_job_postings
                WHERE is_remote = TRUE
            """).fetchone()[0]
        else:
            remote_jobs = None

        # Preserve the complete skill bridge as the daily
        # snapshot source, without assuming dimension column names.
        skill_relationships = gold.execute("""
            SELECT COUNT(*)
            FROM bridge_job_skills
        """).fetchone()[0]

        skill_counts = gold.execute("""
            SELECT
                s.skill_name,
                COUNT(DISTINCT (b.source, b.job_id))
                    FILTER (WHERE b.skill_key IS NOT NULL) AS job_count
            FROM dim_skills AS s
            LEFT JOIN bridge_job_skills AS b
                ON s.skill_key = b.skill_key
            GROUP BY s.skill_name
            ORDER BY job_count DESC
        """).fetchall()

    with sqlite3.connect(SNAPSHOT_DB) as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS daily_market_snapshots (
                snapshot_date TEXT PRIMARY KEY,
                total_jobs INTEGER NOT NULL,
                remote_jobs INTEGER,
                total_companies INTEGER NOT NULL,
                total_locations INTEGER NOT NULL,
                skill_relationships INTEGER NOT NULL,
                captured_at TEXT NOT NULL
            )
        """)

        con.execute("""
            INSERT INTO daily_market_snapshots (
                snapshot_date,
                total_jobs,
                remote_jobs,
                total_companies,
                total_locations,
                skill_relationships,
                captured_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(snapshot_date)
            DO UPDATE SET
                total_jobs = excluded.total_jobs,
                remote_jobs = excluded.remote_jobs,
                total_companies = excluded.total_companies,
                total_locations = excluded.total_locations,
                skill_relationships = excluded.skill_relationships,
                captured_at = excluded.captured_at
        """, (
            snapshot_date,
            total_jobs,
            remote_jobs,
            total_companies,
            total_locations,
            skill_relationships,
            datetime.now(timezone.utc).isoformat(),
        ))

        con.execute("""
            CREATE TABLE IF NOT EXISTS daily_skill_snapshots (
                snapshot_date TEXT NOT NULL,
                skill_name TEXT NOT NULL,
                job_count INTEGER NOT NULL,
                PRIMARY KEY (snapshot_date, skill_name)
            )
        """)

        # Replace this date's skill counts in the market snapshot transaction.
        con.execute("""
            DELETE FROM daily_skill_snapshots
            WHERE snapshot_date = ?
        """, (snapshot_date,))
        con.executemany("""
            INSERT INTO daily_skill_snapshots (
                snapshot_date,
                skill_name,
                job_count
            )
            VALUES (?, ?, ?)
        """, [
            (snapshot_date, skill_name, job_count)
            for skill_name, job_count in skill_counts
        ])

    print(f"Snapshot saved for {snapshot_date}")
    print(f"Total jobs: {total_jobs}")
    print(f"Remote jobs: {remote_jobs}")
    print(f"Companies: {total_companies}")
    print(f"Locations: {total_locations}")
    print(f"Skill relationships: {skill_relationships}")


if __name__ == "__main__":
    create_daily_snapshot()
