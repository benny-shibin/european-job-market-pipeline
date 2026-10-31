
import sqlite3

from src.job_market.ingestion.incremental import (
    initialize_database,
    classify_job,
)


def test_job_history_versions():
    con = sqlite3.connect(":memory:")
    initialize_database(con)

    job = {
        "slug": "python-engineer",
        "title": "Python Engineer",
    }

    t1 = "2026-10-31T10:00:00+00:00"
    t2 = "2026-10-31T11:00:00+00:00"
    t3 = "2026-10-31T12:00:00+00:00"

    assert classify_job(con, job, t1) == "new"
    assert classify_job(con, job, t2) == "unchanged"

    updated_job = {
        **job,
        "title": "Senior Python Engineer",
    }

    assert classify_job(con, updated_job, t3) == "updated"

    history = con.execute("""
        SELECT version, change_type
        FROM job_history
        ORDER BY version
    """).fetchall()

    assert history == [
        (1, "new"),
        (2, "updated"),
    ]

    state = con.execute("""
        SELECT first_seen, last_seen, version
        FROM job_state
        WHERE job_id = 'python-engineer'
    """).fetchone()

    assert state == (t1, t3, 2)

    con.close()
