# Data dictionary and lineage

All identity comparisons use `(source, job_id)`. IDs are source-native strings: Arbeitnow slug (URL fallback), Jobicy integer ID converted to text. Equal IDs from different sources remain separate jobs. Timestamps use UTC unless explicitly labelled as local scheduler clock time.

## Bronze

`data/bronze/<source>/date=YYYY-MM-DD/run=<uuid>/page_<n>.json` retains source, run_id, page, UTC ingested_at and the unchanged provider payload. A temporary write, fsync and same-directory rename publishes a page before state commits. manifest.json exports the run scope, status, timestamps, fetched page numbers, classifications and changed ID/version records. SQLite is authoritative if an export is interrupted.

## Ingestion state (`data/state/jobs.sqlite`)

| Table | Key | Meaning |
| --- | --- | --- |
| job_state | source, job_id | Latest meaningful content hash, first_seen, last_seen, version and lifecycle_state |
| job_history | source, job_id, version | Full source-normalized job JSON for every new/changed version, hash, detected_at and change_type |
| ingestion_runs | run_id | Source, start/finish, status, page cap, fetched count, pagination_complete, counts_json and error |
| ingestion_pages | run_id, page | Durable page path and counts_json committed with classification |
| run_changes | run_id, source, job_id, version | Durable exportable change feed; change_type new or updated |
| run_observations | run_id, source, job_id | Identities seen in each fetch |
| lifecycle_events | run_id, source, job_id | Visibility transitions and detected_at |

Hashing canonicalizes JSON and excludes `views`, `clicks`, `last_seen` and `fetched_at`. Other source changes, including source publication time edits, remain meaningful. Jobicy history stores normalized records; Bronze retains original fields. `first_seen` and `last_seen` are collection observations, not original publication/closure times. A job with unchanged content updates last_seen without a new content version.

Lifecycle values: `observed` and `not_observed`. Only an end-to-end successful, pagination-complete traversal can transition absent identities to not_observed. Partial, failed, or capped traversals never establish absence. A reappearance restores observed without increasing version unless content changed. `confirmed_closed` is reserved for a future explicit provider confirmation integration; nothing currently assigns it. The seven-day Jobicy feed can lose an open listing because of age.

## Silver

`data/silver/processing.sqlite` is the persistent composite-key job store. `jobs` stores normalized row JSON/hash/content observation; `pages` checkpoints immutable Bronze file signatures; `pending_exports` and `staging_rows` make failed exports retryable. See [incremental design](incremental-processing.md).

Source-local `jobs.parquet` is the latest-content export; `staging.parquet` contains the latest batch of changed records, not the whole source history. Empty/no-op runs leave prior exports untouched. Source-local `skills.sqlite` stores text/dictionary fingerprints and skill rows, with a durable dirty export flag; `job_skills.parquet` exports relationships.

| Job column | Type | Meaning |
| --- | --- | --- |
| source, job_id | string | Composite original identity |
| title, company, location | nullable string | Adapter-normalized provider text; location is source eligibility/text, not geocoded |
| remote | nullable boolean | Explicit source remote flag; Jobicy remote-only feed maps to true |
| url | nullable string | Canonical source posting URL retained through UI/exports |
| ingested_at | timestamp UTC | Observation that supplied latest changed content; unchanged polling preserves it |
| description | nullable string | Source HTML/text, searched as provided |
| created_at | nullable integer | Provider publication time as Unix seconds, distinct from ingestion time |
| tags | list of string | Source tags / Jobicy industry labels |
| content_hash | string | Meaningful source content fingerprint |
| version | integer | Silver replay content version; operational source versions are authoritative in job_history/run_changes |

Silver versions track replay order and may differ from ingestion versions when bootstrapping legacy Bronze with repeated historical observations. Do not join the two counters. The operational durable change feed carries authoritative ID/version pairs. New/changed Silver rows are applied only when the incoming timestamp is not older than stored content; equal-time observations use deterministic sorted file traversal.

Skill output grain is source/job/skill_name. Keyword matching uses escaped, case-insensitive non-word boundaries across title, description and tags. A mention is not necessarily a requirement; negation and HTML are not semantically interpreted. Changing config/skills.yaml invalidates extraction fingerprints. Zero-match jobs have no relationships, and empty exports retain a defined schema.

## Gold (`data/gold/job_market.duckdb`)

| Table | Grain / columns |
| --- | --- |
| silver_jobs | Persistent copy of latest Silver content; same job columns |
| fact_job_postings | Composite job identity, title, company/location/date keys, remote, URL, ingestion time |
| dim_companies | Stable company_key and exact company_name |
| dim_locations | Stable location_key and source location_name |
| dim_dates | YYYYMMDD date_key, full_date, year/month/day/quarter from content observation date |
| dim_skills | Stable skill_key and canonical skill_name |
| bridge_job_skills | Unique source/job/skill_key relationships |
| _incremental_model, _model_files | Internal migration/model fingerprints |

Keys are append-only and stable after initial migration. Unused members remain; COUNT(*) on a dimension includes retained values, not only currently referenced values. Critical FK and bridge integrity are checked inside the Gold transaction before commit. See `sql/models.sql` for reusable analytical views.

## Daily snapshots (`data/state/snapshots.sqlite`)

| Table | Primary key | Measures |
| --- | --- | --- |
| daily_market_snapshots | snapshot_date | total_jobs, remote_jobs (nullable), total_companies, total_locations, skill_relationships, captured_at |
| daily_skill_snapshots | snapshot_date, skill_name | Distinct composite jobs mentioning that skill, including zero-count retained skills |
| daily_source_market_snapshots | snapshot_date, source | total_jobs, remote_jobs |
| daily_source_skill_snapshots | snapshot_date, source, skill_name | Distinct jobs mentioning each skill within a source |

snapshot_date is the UTC capture day. Same-day reruns replace that day's aggregates in one SQLite transaction, removing stale skills. Older days remain intact. captured_at records actual UTC capture time. Skill counts and source totals are validated against the Gold read used for capture. These aggregates are not full historical warehouse copies, and missing days are not filled or interpolated. Source-filtered history excludes legacy aggregate-only dates because no accurate breakdown can be inferred.

## Quality and status

Run IDs from the CLI group JSON quality reports, stage logs and latest_run.json. Critical schema, nonnull/blank IDs, uniqueness, timestamp/boolean types, URL, count bounds, Gold FKs/bridge and snapshot consistency failures stop publication. Latest content-age checks are advisory because unchanged records retain content observation time; use run status and last_seen for polling freshness. No fixed minimum daily source volume beyond a nonempty available dataset is assumed. Review unusually low counts against API scope instead of assuming a market collapse.

## Dashboard location grouping

Dashboard filters, location totals, rankings and listings use canonical geographic labels. For example, `Berlin`, `Berlin Office`, `DE - Berlin`, and `Berlin, Germany` share `Berlin, Germany`; `Munich`/`München`, `Zurich`/`Zürich`, and country aliases are grouped similarly. Case, whitespace, office/HQ wrappers, and recognized administrative-region variants normalize consistently. Known country/region lists are deduplicated and ordered consistently. Unresolved small-place labels receive conservative text cleanup; the dashboard does not infer coordinates or collapse them to a country.

Cities include their country to avoid ambiguous labels, and city versus country scope remains separate. Pure remote labels share `Remote / unspecified`; blank/missing labels share `Unknown location`. Those two non-geographic groups remain filterable but are excluded from the geographic location metric/ranking. Geographic cleanup does not infer or change a posting's remote flag.

`Original location` in the listing table and CSV preserves source text. Each posting retains its `(source, job_id)` and appears once even for multi-location labels. Globe aggregation uses the retained raw label and the same alias rules; ambiguous locations remain unplaced. Existing raw filter selections are migrated to their canonical equivalents on rerun.

This grouping is a presentation layer: source records, Silver/Gold dimensions, warehouse SQL, content hashes, and historical snapshots retain their existing values. Existing historical location totals are not recalculated.
