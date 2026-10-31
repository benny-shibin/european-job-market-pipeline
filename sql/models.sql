-- DuckDB models: counts describe accumulated last-observed source samples.
CREATE OR REPLACE VIEW skill_demand AS
SELECT f.source, s.skill_name, COUNT(DISTINCT (b.source, b.job_id)) AS job_count,
       COUNT(DISTINCT (b.source, b.job_id))::DOUBLE /
       NULLIF((SELECT COUNT(*) FROM fact_job_postings x WHERE x.source=f.source), 0) AS share_of_source_jobs
FROM bridge_job_skills b JOIN fact_job_postings f USING(source, job_id)
JOIN dim_skills s USING(skill_key)
GROUP BY f.source, s.skill_name;

CREATE OR REPLACE VIEW skill_cooccurrence AS
SELECT a.source, sa.skill_name AS skill_a, sb.skill_name AS skill_b,
       COUNT(*) AS job_count
FROM bridge_job_skills a JOIN bridge_job_skills b
  ON a.source=b.source AND a.job_id=b.job_id AND a.skill_key < b.skill_key
JOIN dim_skills sa ON a.skill_key=sa.skill_key
JOIN dim_skills sb ON b.skill_key=sb.skill_key
GROUP BY a.source, sa.skill_name, sb.skill_name;

CREATE OR REPLACE VIEW geography_summary AS
SELECT source, COALESCE(l.location_name, 'Unknown location') AS location_name,
       COUNT(*) AS job_count, COUNT(*) FILTER (WHERE remote=TRUE) AS remote_jobs,
       COUNT(*) FILTER (WHERE remote=FALSE) AS not_marked_remote_jobs,
       COUNT(*) FILTER (WHERE remote IS NULL) AS unknown_remote_jobs
FROM fact_job_postings f LEFT JOIN dim_locations l USING(location_key)
GROUP BY source, l.location_name;

CREATE OR REPLACE VIEW company_summary AS
SELECT source, c.company_name, COUNT(*) AS job_count,
       COUNT(*) FILTER (WHERE remote=TRUE) AS remote_jobs
FROM fact_job_postings f LEFT JOIN dim_companies c USING(company_key)
GROUP BY source, c.company_name;

CREATE OR REPLACE VIEW remote_skill_summary AS
SELECT f.source, s.skill_name, f.remote, COUNT(*) AS job_count
FROM fact_job_postings f JOIN bridge_job_skills b USING(source, job_id)
JOIN dim_skills s USING(skill_key)
GROUP BY f.source, s.skill_name, f.remote;

CREATE OR REPLACE VIEW posting_date_summary AS
SELECT source, CAST(TIMEZONE('UTC', TO_TIMESTAMP(created_at)) AS DATE) AS posting_date,
       COUNT(*) AS job_count
FROM silver_jobs WHERE created_at IS NOT NULL
GROUP BY source, posting_date;

-- Candidates only: exact normalized text equality can still join distinct roles.
-- Preserve both identities and all provenance; do not subtract these from totals.
CREATE OR REPLACE VIEW duplicate_candidates AS
SELECT a.source AS source_a, a.job_id AS job_id_a,
       b.source AS source_b, b.job_id AS job_id_b,
       'exact normalized title/company/location' AS match_reason
FROM silver_jobs a JOIN silver_jobs b
 ON a.source < b.source
 AND LOWER(TRIM(a.title))=LOWER(TRIM(b.title))
 AND LOWER(TRIM(a.company))=LOWER(TRIM(b.company))
 AND LOWER(TRIM(a.location))=LOWER(TRIM(b.location))
WHERE LENGTH(TRIM(a.title)) > 0 AND LENGTH(TRIM(a.company)) > 0
 AND LENGTH(TRIM(a.location)) > 0;
