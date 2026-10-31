
import sqlite3

from src.job_market.ingestion.incremental import (
    classify_job,
    initialize_database,
)


def test_new_unchanged_updated():
    con = sqlite3.connect(":memory:")
    initialize_database(con)

    job = {
        "slug": "data-engineer-001",
        "title": "Data Engineer",
    }

    assert classify_job(con, job, "2026-10-31T10:00:00Z") == "new"

    assert classify_job(con, job, "2026-10-31T11:00:00Z") == "unchanged"

    updated_job = {
        "slug": "data-engineer-001",
        "title": "Senior Data Engineer",
    }

    assert (
        classify_job(
            con,
            updated_job,
            "2026-10-31T12:00:00Z",
        )
        == "updated"
    )

    row = con.execute("""
        SELECT version
        FROM job_state
        WHERE source = ? AND job_id = ?
    """, ("arbeitnow", "data-engineer-001")).fetchone()

    assert row[0] == 2

    con.close()


def test_missing_job_id():
    con = sqlite3.connect(":memory:")
    initialize_database(con)

    result = classify_job(
        con,
        {"title": "Unknown job"},
        "2026-10-31T10:00:00Z",
    )

    assert result == "invalid"
    con.close()
