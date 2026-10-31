# Reproducible analytics

All views below are installed transactionally from `sql/models.sql` when Gold builds. They describe accumulated last-observed source samples. They are not whole-market estimates or current open-vacancy counts. Missing observations, source page caps, feed eligibility and publication-window changes can all affect counts.

| View | Definition / denominator |
| --- | --- |
| skill_demand | Distinct composite jobs mentioning a skill, grouped by source. Share divides by all fact jobs for that source, including jobs with no detected skills. Multi-skill shares can exceed 100% when summed. |
| skill_cooccurrence | Unordered skill pairs appearing in the same source/job, one row per job/pair. Pair orientation follows stable skill keys, not alphabetic order. |
| remote_skill_summary | Detected jobs by source, skill and three-valued remote status. False means not marked remote, never confirmed on-site. |
| geography_summary | Source-reported location/eligibility, total and true/false/unknown remote counts. Locations are not normalized coordinates. |
| company_summary | Exact company names by source; no entity resolution. |
| posting_date_summary | Source publication dates from created_at Unix seconds, excluding missing dates. This differs from dim_dates, which follows content observation time. |
| duplicate_candidates | Exact normalized title/company/location matches across sources, retained as candidates without dropping any job. |

Examples in DuckDB:

```sql
SELECT source, skill_name, job_count, ROUND(100 * share_of_source_jobs, 1) AS percent
FROM skill_demand ORDER BY job_count DESC, source, skill_name;

SELECT source, skill_a, skill_b, job_count
FROM skill_cooccurrence ORDER BY job_count DESC;

SELECT source, location_name, job_count, remote_jobs,
       100.0 * remote_jobs / NULLIF(job_count, 0) AS remote_percentage
FROM geography_summary ORDER BY job_count DESC;
```

Historical SQL runs separately in SQLite (`data/state/snapshots.sqlite`):

```sql
SELECT snapshot_date, total_jobs,
       100.0 * remote_jobs / NULLIF(total_jobs, 0) AS remote_percentage
FROM daily_market_snapshots ORDER BY snapshot_date;
```

`sql/skill_trends.sql` compares canonical skills by UTC snapshot date. Source-level versions allow separate source comparisons; combined totals are a sum of provider observations and can contain cross-source duplicates. Daily remote percentage uses all collected postings as denominator; unknown remote counts and zero-job dates have no computed percentage. A single date is a point, not evidence of a trend. Missing skill/date combinations are unknown and left as gaps rather than imputed as zero.

Company and location dimensions retain unused members to keep stable keys. Use fact-joined summaries above for referenced-member counts; the market snapshot's dimension totals intentionally retain the existing COUNT(*) dimension contract.

## Dashboard country statistics

The country ranking and expandable summary use the current sidebar filters, including the country selector. City labels and country-wide labels roll up to one canonical country, with one count per `(source, job_id)`. Multiple cities in the same identifiable country can contribute once; multi-country, broad regional, unknown, or conflicting city/country scopes remain unassigned. An explicit country can be counted even when its smaller city is absent from the offline gazetteer, so country coverage can exceed precise globe-pin coverage.

The summary shows postings, explicitly remote postings, exact distinct company names, remote share (remote / assigned postings in that country), and share of all filtered postings. The latter includes unassigned postings in its denominator, so country shares need not sum to 100%. Unknown remote flags are not counted as explicitly remote. These are sampled source location/eligibility counts, not verified office addresses or whole-market demand estimates.

Country names are enabled on the globe by default. The major-country view reduces label overlap; choose All countries for more detail after zooming. Country labels remain enabled; there is no visibility toggle. The default camera is closer to Europe, while subsequent rotation/zoom persists during filtering.

Country-share highlights show the top three countries as percentages of **all filtered distinct source/job records**, with proportional tracks and gaps to the next country in percentage points. Equal shares display a tie. A 100% strip includes the top countries, other assigned countries and unassigned/multi-country records. The ranking defaults to percentage share and can switch to posting counts; both modes retain counts and share labels. The remote overlay uses the same global denominator as the country bar, while hover details separately describe the remote percentage within a country. These comparisons are across countries in the current sample, not historical changes.
