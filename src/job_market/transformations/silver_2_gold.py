
from pathlib import Path

import duckdb

SILVER_PATH = "data/silver/arbeitnow/jobs.parquet"
GOLD_PATH = Path("data/gold/job_market.duckdb")


def build_gold():
    GOLD_PATH.parent.mkdir(parents=True, exist_ok=True)

    con = duckdb.connect(str(GOLD_PATH))

    try:
        con.execute(f"""
            CREATE OR REPLACE TABLE silver_jobs AS
            SELECT *
            FROM read_parquet('{SILVER_PATH}')
        """)

        # Company dimension
        con.execute("""
            CREATE OR REPLACE TABLE dim_companies AS
            SELECT
                ROW_NUMBER() OVER (ORDER BY company) AS company_key,
                company AS company_name
            FROM (
                SELECT DISTINCT company
                FROM silver_jobs
                WHERE company IS NOT NULL
            )
        """)

        # Location dimension
        con.execute("""
            CREATE OR REPLACE TABLE dim_locations AS
            SELECT
                ROW_NUMBER() OVER (ORDER BY location) AS location_key,
                location AS location_name
            FROM (
                SELECT DISTINCT location
                FROM silver_jobs
                WHERE location IS NOT NULL
            )
        """)

        # Date dimension
        con.execute("""
            CREATE OR REPLACE TABLE dim_dates AS
            SELECT
                CAST(STRFTIME(job_date, '%Y%m%d') AS INTEGER)
                    AS date_key,
                job_date AS full_date,
                YEAR(job_date) AS year,
                MONTH(job_date) AS month,
                DAY(job_date) AS day,
                QUARTER(job_date) AS quarter
            FROM (
                SELECT DISTINCT CAST(ingested_at AS DATE) AS job_date
                FROM silver_jobs
            )
        """)

        # Fact table
        con.execute("""
            CREATE OR REPLACE TABLE fact_job_postings AS
            SELECT
                j.source,
                j.job_id,
                j.title,
                c.company_key,
                l.location_key,
                d.date_key,
                j.remote,
                j.url,
                j.ingested_at
            FROM silver_jobs j
            LEFT JOIN dim_companies c
                ON j.company = c.company_name
            LEFT JOIN dim_locations l
                ON j.location = l.location_name
            LEFT JOIN dim_dates d
                ON CAST(j.ingested_at AS DATE) = d.full_date
        """)
        # skill dimension

        # Skill dimension
        con.execute("""
            CREATE OR REPLACE TABLE dim_skills AS
            SELECT
                ROW_NUMBER() OVER (
                    ORDER BY skill_name
                ) AS skill_key,
                skill_name
            FROM (
                SELECT DISTINCT skill_name
                FROM read_parquet(
                    'data/silver/arbeitnow/job_skills.parquet'
                )
                WHERE skill_name IS NOT NULL
            )
        """)

        # Job-to-skill bridge table
        con.execute("""
            CREATE OR REPLACE TABLE bridge_job_skills AS
            SELECT DISTINCT
                f.source,
                f.job_id,
                s.skill_key
            FROM read_parquet(
                'data/silver/arbeitnow/job_skills.parquet'
            ) js
            JOIN fact_job_postings f
                ON js.source = f.source
                AND js.job_id = f.job_id
            JOIN dim_skills s
                ON js.skill_name = s.skill_name
        """)

        print("\nGOLD LAYER CREATED")
        print("-" * 40)

        for table in [
            "dim_companies",
            "dim_locations",
            "dim_dates",
            "fact_job_postings",
            "dim_skills",           
            "bridge_job_skills", 
        ]:
            count = con.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0]

            print(f"{table}: {count} records")

    finally:
        con.close()


if __name__ == "__main__":
    build_gold()
