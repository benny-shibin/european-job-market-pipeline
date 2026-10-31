# Incremental processing and recovery

## Before migration

Bronze appended raw pages. Ingestion committed SQLite classification per page but had no run/page ledger; a crash after a page write left state behind. Silver scanned every Bronze file, deduplicated all observations and overwrote jobs.parquet. Skills re-extracted every job and replaced job_skills.parquet. Gold replaced every dimension, fact, and bridge separately; surrogate keys could change between runs.

## Current contract

Identity is always `(source, job_id)`, with native source IDs retained. Ingestion writes raw pages atomically before applying SQLite state, history, per-run changes, observations, and a page checkpoint in one transaction. `ingestion_runs` and each run's `manifest.json` record status, scope, timestamps, pages and changes. A capped traversal is successful but `pagination_complete=false`. A new invocation replays uncommitted durable pages before fetching; no committed page increments versions twice. Failed traversals never mark missing jobs absent.

Silver's `data/silver/processing.sqlite` contains the persistent job store, page signatures and pending exports. Only new pages are parsed, only changed job rows are upserted. Changed rows also export to source-local staging.parquet; source-local jobs.parquet remains a compatibility/export artifact. Export failure leaves a pending marker so the next invocation retries it. These Parquet exports rewrite a source on content changes; the authoritative store updates only affected jobs. No-change runs leave Parquet files untouched. Bronze pages must be immutable; signatures use path, size and nanosecond mtime.

Skills keep per-job text/dictionary fingerprints and relationships in source-local skills.sqlite. Only changed text or dictionary definitions require extraction. Dirty export markers survive failure. Changing the dictionary deliberately re-extracts jobs.

Gold scans input projections to identify changes (it does not yet use a columnar change-feed index). It updates affected fact/bridge rows inside one DuckDB transaction and appends stable dimension keys. Unused dimension members remain to preserve keys. Initial migration rebuilds the legacy warehouse once. Subsequent no-change builds make no persistent model changes. Integrity checks run before commit. Daily snapshot SQLite storage is independent and never rebuilt by Gold.

This is incremental transformation, not zero-I/O processing: comparisons still read Silver input. Console counters and integration tests measure parsed pages, extracted jobs and Gold upserts. Do not equate accumulated last-observed jobs with active vacancies.

## Recovery

Run `python -m src.job_market.cli resume` to replay stored Bronze through downstream stages without another API fetch. A normal run also recovers saved pages before requesting new data. SQLite transactions and atomic rename require all storage to be on the same local filesystem. A CLI lock prevents cooperating pipeline processes overlapping. Do not run standalone writers concurrently; stop the dashboard before warehouse writes if it holds a DuckDB connection.

`manifest.json` is a reviewable export; SQLite is authoritative when a crash occurs between commit and JSON export. `run_changes` is the durable changed/new ID and version feed. Keep Bronze, jobs.sqlite and Silver processing.sqlite together when backing up. Do not restore checkpoints without their associated data stores. To rebuild Silver from immutable Bronze, move aside the entire Silver directory, then run `resume`; Gold updates itself from the rebuilt exports.
