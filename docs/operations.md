# Running and maintaining the project

## Refresh data

From the repository root with the virtual environment activated, run:

```bash
python -m src.job_market.ingestion.arbeitnow
python -m src.job_market.transformations.bronze_2_silver
python -m src.job_market.quality.validators
python -m src.job_market.transformations.extract_skills
python -m src.job_market.transformations.silver_2_gold
```

Stop if any command fails. The scripts are independent; running them in the wrong order can reuse stale outputs. Stop the dashboard before rebuilding Gold, then restart it. To collect a different page limit, use the function explicitly:

```bash
python -c 'from src.job_market.ingestion.arbeitnow import ingest_jobs; ingest_jobs(max_pages=5)'
```

Larger limits increase requests and collection time. Pagination ends early when the source indicates no next page.

## Query the warehouse

Use Python without requiring the DuckDB CLI:

```bash
python - <<'PY'
from pathlib import Path
import duckdb
with duckdb.connect('data/gold/job_market.duckdb', read_only=True) as con:
    for query in Path('sql/analytics.sql').read_text().split(';'):
        if query.strip():
            print(con.execute(query).df().to_string(index=False))
PY
```

## Troubleshooting

| Symptom | Action |
| --- | --- |
| Module or dependency missing | Activate `.venv`, install requirements, and run commands from the root |
| HTTP request fails | Check network/source availability; inspect saved pages before retrying because partial runs remain |
| No Bronze records available | Run ingestion first; no Silver file is created for empty input |
| Silver quality check fails | Read the printed check report; inspect and correct the upstream cause before rebuilding |
| Missing `job_skills.parquet` | Run skill extraction before Gold |
| Dashboard says Gold database not found | Run the complete pipeline in order |
| DuckDB lock error | Stop other readers/writers, especially the dashboard, and retry |
| Dashboard appears stale | Finish the rebuild, refresh, or restart Streamlit; reads are cached |
| Light controls on dark panels | Restart from the root to load `.streamlit/config.toml`; check the browser's Streamlit theme |

## Retention and rebuilds

Bronze contains full descriptions and source payloads and grows with every run. Silver and Gold are rebuilt from all retained Bronze files. Back up Bronze before changing retention; removing older runs changes the reconstructable history. Never remove generated data just to resolve a test failure: the test suite uses its own temporary directory.

The default collector has no persisted checkpoint. There is no scheduler, retry policy, expired-listing cleanup, migration system, or production hosting configuration. Gold builds are not atomic. For automated operation, arrange scheduling and failure monitoring separately and prevent overlapping refreshes.

## Repository hygiene

Keep `.venv/`, generated `data/`, Python caches, and secrets out of Git. Runtime dependencies are pinned in `requirements.txt`; development dependencies are in `requirements-dev.txt`. When changing versions, rerun the suite in a clean Python 3.12 environment. Commit source, configuration, documentation, and tests together.

## Optional state-tracked ingestion

`src/job_market/ingestion/incremental.py` provides an alternative collector:

```bash
python -m src.job_market.ingestion.incremental
```

It writes the same Bronze envelope format and maintains `data/state/jobs.sqlite`. The `job_state` table stores composite source/job identity, SHA-256 content hash, first/last seen timestamps, and a version counter. Reports classify observed records as new, updated, unchanged, or invalid (missing identity). Every fetched page is still archived, including unchanged jobs. The collector saves each page through a temporary file and rename before committing state for that page. There is no deletion detection or cross-file/database transaction; interrupted runs can leave archived pages ahead of state. Back up state with Bronze if retaining classifications matters. Downstream transformation commands remain the same.
