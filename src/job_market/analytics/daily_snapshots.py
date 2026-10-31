
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from src.job_market.quality.reports import publish_report


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

        remote_column = 'remote' if 'remote' in columns else 'is_remote' if 'is_remote' in columns else None
        remote_expression = f'SUM(CASE WHEN {remote_column} THEN 1 ELSE 0 END)' if remote_column else 'NULL'
        source_counts = gold.execute(f"""
            SELECT source, COUNT(*), {remote_expression}
            FROM fact_job_postings GROUP BY source
        """).fetchall()
        source_skills = gold.execute("""
            SELECT b.source, s.skill_name, COUNT(DISTINCT (b.source, b.job_id))
            FROM bridge_job_skills b JOIN dim_skills s USING(skill_key)
            GROUP BY b.source, s.skill_name
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

        con.execute("""CREATE TABLE IF NOT EXISTS daily_source_market_snapshots (
            snapshot_date TEXT, source TEXT, total_jobs INTEGER NOT NULL, remote_jobs INTEGER,
            PRIMARY KEY(snapshot_date, source))""")
        con.execute("""CREATE TABLE IF NOT EXISTS daily_source_skill_snapshots (
            snapshot_date TEXT, source TEXT, skill_name TEXT, job_count INTEGER NOT NULL,
            PRIMARY KEY(snapshot_date, source, skill_name))""")
        con.execute('DELETE FROM daily_source_market_snapshots WHERE snapshot_date=?', (snapshot_date,))
        con.execute('DELETE FROM daily_source_skill_snapshots WHERE snapshot_date=?', (snapshot_date,))
        con.executemany('INSERT INTO daily_source_market_snapshots VALUES (?, ?, ?, ?)',
                        [(snapshot_date, *row) for row in source_counts])
        con.executemany('INSERT INTO daily_source_skill_snapshots VALUES (?, ?, ?, ?)',
                        [(snapshot_date, *row) for row in source_skills])
        stored = con.execute('SELECT total_jobs, remote_jobs FROM daily_market_snapshots WHERE snapshot_date=?', (snapshot_date,)).fetchone()
        checks = dict(
            market_matches_gold=stored == (total_jobs, remote_jobs),
            skill_counts_valid=all(0 <= count <= total_jobs for _, count in skill_counts),
            skill_rows_unique=len(skill_counts) == len({name for name, _ in skill_counts}),
            source_totals_match=sum(row[1] for row in source_counts) == total_jobs,
            remote_count_valid=remote_jobs is None or 0 <= remote_jobs <= total_jobs,
            source_remote_totals_match=remote_jobs is None or sum(row[2] for row in source_counts) == remote_jobs,
            stored_skills_match_gold=sorted(con.execute('SELECT skill_name, job_count FROM daily_skill_snapshots WHERE snapshot_date=?', (snapshot_date,)).fetchall()) == sorted(skill_counts),
        )
        publish_report('snapshots', checks, details=dict(snapshot_date=snapshot_date))

    print(f"Snapshot saved for {snapshot_date}")
    print(f"Total jobs: {total_jobs}")
    print(f"Remote jobs: {remote_jobs}")
    print(f"Companies: {total_companies}")
    print(f"Locations: {total_locations}")
    print(f"Skill relationships: {skill_relationships}")


if __name__ == "__main__":
    create_daily_snapshot()
