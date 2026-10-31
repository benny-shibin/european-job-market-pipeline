# Testing and validation

```sh
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pytest -q
# Portable container validation:
docker compose run --build --rm tests
```

All automated tests use pytest temporary storage and mocked HTTP boundaries. They do not alter repository data or call live APIs. CI runs on Linux/Python 3.12 and builds/runs the Docker test target. Dependencies and the container base image are pinned.

| Test module | Coverage |
| --- | --- |
| test_e2e.py | Real Bronze/Silver/Gold subprocess transforms, schema rejection, pagination, original dashboard filter/reset smoke tests, zero skill mentions |
| test_incremental_downstream.py | Changed/no-change record counts, untouched Parquet bytes/mtimes, stable unrelated facts and dimension keys, manifest ID/version export, crash replay, repeated runs, pending export recovery, partial/failed visibility scopes, reappearance, volatile content, two-source identity and SQL models, Gold rollback, schema/type errors and machine-readable failed quality report |
| test_dashboard_history.py | One/multiple snapshot dates, chart traces, skill/date filters, read-only SQLite arguments, missing snapshots and skills, zero jobs and unknown remote counts |
| test_dashboard_visuals.py | Globe resolution/ambiguity, source/job deduplication, zero-result globe, light/dark palettes, lollipop/mosaic charts, heatmap, motion preferences and filter integration |
| test_location_normalization.py | City/country aliases, administrative variants, remote/missing labels, idempotence, scope separation, raw-label retention and canonical filter/chart/globe integration |
| test_country_statistics.py | Country attribution, deduplicated country totals, unknown/nullable flags, camera zoom, major/all label modes and country-filter integration |
| test_operations.py | OS-lock overlap rejection/release, stage failures and recovery status, generated plist command/calendar semantics, isolated synthetic demo |
| test_incremental.py, test_job_history.py, test_skill_extraction.py | Classification versions, first/last seen, durable full content history, dictionary matching boundaries |

The offline tests exercise real local files and transactions, not a mock warehouse. Critical checks produce reports before raising, and Gold rolls back failures before publication. Production data must pass the same validators.

Manual acceptance checks: generate the isolated demo, inspect overview and historical tabs at wide/narrow widths, run the CLI manually, install/inspect launchd, inspect a scheduled invocation and logs, build containers and test the dashboard health endpoint. Verify volume persistence after stopping/recreating containers. Source API availability, live-schema changes, real sleep/wake behavior and daylight saving scheduling cannot be proven by offline tests. launchd provides best-effort daily local execution, not an always-on service guarantee.

AppTest does not verify browser layout, animation, contrast, downloads, keyboard navigation or external source URLs. Screenshots in README use synthetic demo data and are not a record of market activity. See [operations](operations.md) for the live run and schedule commands.
