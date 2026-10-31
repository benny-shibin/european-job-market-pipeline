
-- Top 10 hiring companies
SELECT
    c.company_name,
    COUNT(*) AS total_jobs
FROM fact_job_postings f
JOIN dim_companies c
    ON f.company_key = c.company_key
GROUP BY c.company_name
ORDER BY total_jobs DESC
LIMIT 10;


-- Top 10 hiring locations
SELECT
    l.location_name,
    COUNT(*) AS total_jobs
FROM fact_job_postings f
JOIN dim_locations l
    ON f.location_key = l.location_key
GROUP BY l.location_name
ORDER BY total_jobs DESC
LIMIT 10;


-- Remote vs non-remote jobs
SELECT
    CASE
        WHEN remote = TRUE THEN 'Remote'
        WHEN remote = FALSE THEN 'Not marked remote'
        ELSE 'Unknown'
    END AS work_arrangement,
    COUNT(*) AS total_jobs
FROM fact_job_postings
GROUP BY 1
ORDER BY total_jobs DESC;
