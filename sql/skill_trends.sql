-- Historical demand for technical skills.
-- Run against data/state/snapshots.sqlite.
SELECT
    snapshot_date,
    skill_name,
    job_count
FROM daily_skill_snapshots
WHERE skill_name IN (
    'Python',
    'SQL',
    'AWS',
    'Azure',
    'Docker',
    'Kubernetes'
)
ORDER BY snapshot_date, skill_name;
