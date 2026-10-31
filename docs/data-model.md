# Data model and semantics

## Bronze: source observations

Files: `data/bronze/arbeitnow/date=YYYY-MM-DD/run=<uuid>/page_<n>.json`.

Each envelope contains `source`, `run_id`, UTC `ingested_at`, `page`, and the complete API `payload`. Runs append new files. The default module command fetches at most two pages; collection stops when data is empty or `links.next` is absent. Requests have a 30-second timeout and a two-second pause between pages. HTTP and schema failures raise exceptions; there is no automatic retry.

## Silver: latest observed jobs

File: `data/silver/arbeitnow/jobs.parquet`.

| Column | Meaning |
| --- | --- |
| `source`, `job_id` | Composite job identity; ID uses slug, then URL, then empty string |
| `title`, `company`, `location` | Source-provided text |
| `remote`, `url` | Source remote flag and original posting URL |
| `ingested_at` | UTC collection timestamp |
| `description`, `created_at`, `tags` | Source description, original creation value, and tags |

The builder scans all Bronze pages, removes empty IDs, sorts by ingestion time, and keeps the latest observation per `(source, job_id)`. Equal-timestamp conflicts have no explicit tie-breaker. Output is overwritten each run. No Bronze records means the builder returns without updating any existing Silver file.

Quality checks require a nonempty dataset, nonnull/nonblank IDs, unique composite IDs, and nonnull titles, companies, and collection timestamps. Blank titles/companies, URL validity, and location completeness are not checked. Null company values can be represented in Gold, but the quality gate rejects them.

## Skills

File: `data/silver/arbeitnow/job_skills.parquet` with `source`, `job_id`, `skill_name`.

Extraction searches title, description, and tags with case-insensitive, escaped keyword patterns bounded by non-word characters. Each canonical skill appears at most once per job. Extend `config/skills.yaml` with a skill and its aliases, then rerun extraction and Gold. Jobs with no matches have no bridge rows; an entirely empty skill result is still written with a defined schema.

Descriptions are searched as received; HTML is not stripped. Skill mentions do not distinguish required skills, optional skills, or negation. This is dictionary matching, not semantic inference.

## Gold: analytical warehouse

File: `data/gold/job_market.duckdb`.

| Table | Grain / contents |
| --- | --- |
| `silver_jobs` | Materialized Silver input retained in the database |
| `fact_job_postings` | One row per source/job; title, company/location/date keys, remote flag, URL, ingestion timestamp |
| `dim_companies` | Distinct nonnull company names and generated keys |
| `dim_locations` | Distinct nonnull source location strings and generated keys |
| `dim_dates` | Collection date, integer YYYYMMDD key, year/month/day/quarter |
| `dim_skills` | Distinct detected skills and generated keys |
| `bridge_job_skills` | Unique source/job/skill relationships matching a fact row |

Company, location, and skill keys are regenerated in sorted order and can change between builds. Relationships are established by joins; database constraints are not declared. Missing companies or locations produce null foreign keys through left joins. Dates represent ingestion, not publication. Tables are replaced sequentially, without an encompassing transaction.

## Interpretation

Listings accumulate across historical Bronze runs. There is no expiry/deletion detection, so counts are not a measure of currently open vacancies. Locations are not geocoded or normalized; company names are not entity-resolved. Remote `false` means “Not marked remote,” not confirmed on-site work; missing flags mean “Unknown.” Skill percentages use filtered postings as the denominator and can sum above 100%. The dashboard caches database reads for up to five minutes and uses database file modification time as a cache input.
