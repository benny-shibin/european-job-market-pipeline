"""Transactional Gold upserts with stable dimensions and changed-job bridges."""
from pathlib import Path
import hashlib
import json

import duckdb

from src.job_market.quality.reports import publish_report

GOLD_PATH = Path('data/gold/job_market.duckdb')
SILVER_PATH = 'data/silver/arbeitnow/jobs.parquet'


def check_integrity(con):
    checks = {
        'unique_job_ids': "SELECT COUNT(*) = COUNT(DISTINCT (source, job_id)) FROM fact_job_postings",
        'company_foreign_keys': 'SELECT COUNT(*) = 0 FROM fact_job_postings f LEFT JOIN dim_companies d USING(company_key) WHERE f.company_key IS NOT NULL AND d.company_key IS NULL',
        'location_foreign_keys': 'SELECT COUNT(*) = 0 FROM fact_job_postings f LEFT JOIN dim_locations d USING(location_key) WHERE f.location_key IS NOT NULL AND d.location_key IS NULL',
        'date_foreign_keys': 'SELECT COUNT(*) = 0 FROM fact_job_postings f LEFT JOIN dim_dates d USING(date_key) WHERE d.date_key IS NULL',
        'bridge_job_foreign_keys': 'SELECT COUNT(*) = 0 FROM bridge_job_skills b LEFT JOIN fact_job_postings f USING(source, job_id) WHERE f.job_id IS NULL',
        'bridge_skill_foreign_keys': 'SELECT COUNT(*) = 0 FROM bridge_job_skills b LEFT JOIN dim_skills s USING(skill_key) WHERE s.skill_key IS NULL',
        'unique_bridge': 'SELECT COUNT(*) = COUNT(DISTINCT (source, job_id, skill_key)) FROM bridge_job_skills',
    }
    return {name: bool(con.execute(sql).fetchone()[0]) for name, sql in checks.items()}


def build_gold():
    paths = sorted(Path(SILVER_PATH).parent.parent.glob('*/jobs.parquet'))
    if not paths:
        raise FileNotFoundError('No Silver job datasets')
    skill_paths = [path.with_name('job_skills.parquet') for path in paths]
    if not all(path.exists() for path in skill_paths):
        raise FileNotFoundError('Run skill extraction for all Silver sources first')
    GOLD_PATH.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(GOLD_PATH)) as con:
        con.execute("SET TimeZone='UTC'")
        # Inputs belong to this connection; keep file-backed staging views out
        # of the persistent warehouse catalog, including earlier local builds.
        con.execute('DROP VIEW IF EXISTS incoming_jobs')
        con.execute('DROP VIEW IF EXISTS incoming_skills')
        con.register('incoming_jobs', con.read_parquet([str(path) for path in paths], union_by_name=True))
        con.register('incoming_skills', con.read_parquet([str(path) for path in skill_paths], union_by_name=True))
        initialized = con.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_name='_incremental_model'").fetchone()[0]
        con.execute('BEGIN TRANSACTION')
        try:
            if not initialized:
                # One-time migration from the old replace-table warehouse.
                for name in ['silver_jobs', 'fact_job_postings', 'dim_companies', 'dim_locations', 'dim_dates', 'dim_skills', 'bridge_job_skills']:
                    con.execute(f'DROP TABLE IF EXISTS {name}')
                con.execute('CREATE TABLE silver_jobs AS SELECT * FROM incoming_jobs LIMIT 0')
                con.execute('CREATE TABLE dim_companies (company_key BIGINT PRIMARY KEY, company_name VARCHAR UNIQUE)')
                con.execute('CREATE TABLE dim_locations (location_key BIGINT PRIMARY KEY, location_name VARCHAR UNIQUE)')
                con.execute('CREATE TABLE dim_skills (skill_key BIGINT PRIMARY KEY, skill_name VARCHAR UNIQUE)')
                con.execute('CREATE TABLE dim_dates (date_key INTEGER PRIMARY KEY, full_date DATE, year INTEGER, month INTEGER, day INTEGER, quarter INTEGER)')
                con.execute('''CREATE TABLE fact_job_postings (source VARCHAR, job_id VARCHAR, title VARCHAR,
                    company_key BIGINT, location_key BIGINT, date_key INTEGER, remote BOOLEAN,
                    url VARCHAR, ingested_at TIMESTAMPTZ, PRIMARY KEY(source, job_id))''')
                con.execute('CREATE TABLE bridge_job_skills (source VARCHAR, job_id VARCHAR, skill_key BIGINT, PRIMARY KEY(source, job_id, skill_key))')
                con.execute('CREATE TABLE _incremental_model (version INTEGER)')
                con.execute('INSERT INTO _incremental_model VALUES (1)')
            con.execute('''CREATE TEMP TABLE changed_jobs AS
                SELECT i.* FROM incoming_jobs i LEFT JOIN silver_jobs s USING(source, job_id)
                WHERE s.job_id IS NULL OR i.content_hash IS DISTINCT FROM s.content_hash''')
            con.execute('''CREATE TEMP TABLE changed_skills AS
                SELECT DISTINCT source, job_id FROM (
                    (SELECT source, job_id, skill_name FROM incoming_skills
                     EXCEPT SELECT b.source, b.job_id, s.skill_name FROM bridge_job_skills b JOIN dim_skills s USING(skill_key))
                    UNION ALL
                    (SELECT b.source, b.job_id, s.skill_name FROM bridge_job_skills b JOIN dim_skills s USING(skill_key)
                     EXCEPT SELECT source, job_id, skill_name FROM incoming_skills)
                )''')
            count = con.execute('SELECT COUNT(*) FROM changed_jobs').fetchone()[0]
            skill_count = con.execute('SELECT COUNT(*) FROM changed_skills').fetchone()[0]
            if count or skill_count:
                for table, key, name, values in [
                    ('dim_companies', 'company_key', 'company_name', 'SELECT DISTINCT company AS value FROM changed_jobs'),
                    ('dim_locations', 'location_key', 'location_name', 'SELECT DISTINCT location AS value FROM changed_jobs'),
                    ('dim_skills', 'skill_key', 'skill_name', 'SELECT DISTINCT skill_name AS value FROM incoming_skills')]:
                    con.execute(f'''INSERT INTO {table}
                        SELECT (SELECT COALESCE(MAX({key}), 0) FROM {table}) + ROW_NUMBER() OVER (ORDER BY value), value
                        FROM ({values}) v WHERE value IS NOT NULL
                        AND NOT EXISTS (SELECT 1 FROM {table} d WHERE d.{name}=v.value)''')
                con.execute('''INSERT INTO dim_dates SELECT CAST(STRFTIME(job_date, '%Y%m%d') AS INTEGER), job_date,
                    YEAR(job_date), MONTH(job_date), DAY(job_date), QUARTER(job_date)
                    FROM (SELECT DISTINCT CAST(ingested_at AS DATE) AS job_date FROM changed_jobs) j
                    WHERE NOT EXISTS (SELECT 1 FROM dim_dates d WHERE d.full_date=j.job_date)''')
                con.execute('DELETE FROM silver_jobs USING changed_jobs c WHERE silver_jobs.source=c.source AND silver_jobs.job_id=c.job_id')
                con.execute('INSERT INTO silver_jobs SELECT * FROM changed_jobs')
                con.execute('DELETE FROM fact_job_postings USING changed_jobs c WHERE fact_job_postings.source=c.source AND fact_job_postings.job_id=c.job_id')
                con.execute('''INSERT INTO fact_job_postings SELECT j.source, j.job_id, j.title, c.company_key, l.location_key,
                    d.date_key, j.remote, j.url, j.ingested_at FROM changed_jobs j
                    LEFT JOIN dim_companies c ON j.company=c.company_name
                    LEFT JOIN dim_locations l ON j.location=l.location_name
                    LEFT JOIN dim_dates d ON CAST(j.ingested_at AS DATE)=d.full_date''')
                con.execute('''CREATE TEMP TABLE affected AS SELECT source, job_id FROM changed_jobs
                    UNION SELECT source, job_id FROM changed_skills''')
                con.execute('DELETE FROM bridge_job_skills USING affected a WHERE bridge_job_skills.source=a.source AND bridge_job_skills.job_id=a.job_id')
                con.execute('''INSERT INTO bridge_job_skills SELECT DISTINCT js.source, js.job_id, s.skill_key
                    FROM incoming_skills js JOIN affected USING(source, job_id)
                    JOIN dim_skills s USING(skill_name) JOIN fact_job_postings f USING(source, job_id)''')
            models_path = Path(__file__).resolve().parents[3] / 'sql/models.sql'
            models = models_path.read_text()
            digest = hashlib.sha256(models.encode()).hexdigest()
            con.execute('CREATE TABLE IF NOT EXISTS _model_files (name VARCHAR PRIMARY KEY, digest VARCHAR)')
            if not con.execute('SELECT 1 FROM _model_files WHERE name=? AND digest=?', ('analytics', digest)).fetchone():
                con.execute(models)
                con.execute("INSERT OR REPLACE INTO _model_files VALUES ('analytics', ?)", (digest,))
            checks = check_integrity(con)
            checks.update(dict(
                incoming_jobs_unique=con.execute('SELECT COUNT(*) = COUNT(DISTINCT (source, job_id)) FROM incoming_jobs').fetchone()[0],
                incoming_skill_jobs_exist=con.execute('SELECT COUNT(*) = 0 FROM incoming_skills s LEFT JOIN incoming_jobs j USING(source, job_id) WHERE j.job_id IS NULL').fetchone()[0],
                incoming_skill_names_valid=con.execute("SELECT COUNT(*) = 0 FROM incoming_skills WHERE skill_name IS NULL OR LENGTH(TRIM(skill_name))=0").fetchone()[0],
                fact_matches_silver_count=con.execute('SELECT (SELECT COUNT(*) FROM fact_job_postings) = (SELECT COUNT(*) FROM silver_jobs)').fetchone()[0],
            ))
            publish_report('gold', checks, details=dict(changed_jobs=count, changed_skill_sets=skill_count))
            con.execute('COMMIT')
        except BaseException:
            con.execute('ROLLBACK')
            raise
    print(json.dumps(dict(event='stage_metrics', changed_jobs=count, changed_skill_sets=skill_count)))
    print(f'Gold: {count} job rows upserted; {skill_count} job skill sets changed')
    return dict(changed_jobs=count, changed_skills=skill_count)


if __name__ == '__main__':
    build_gold()
