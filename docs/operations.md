# Operations and recovery

Run from the repository root with `.venv` activated. All operational timestamps are UTC; launchd calendar time follows the Mac's system timezone.

```sh
python -m src.job_market.cli run --sources arbeitnow jobicy
# Transform already saved data, recover state, and retry failed downstream work:
python -m src.job_market.cli resume
# Open the dashboard:
streamlit run dashboard/app.py
```

Default CLI `run` without `--sources` collects only Arbeitnow. Sources can run separately: `--sources jobicy`. `--max-pages 5` changes the page cap for each selected source; only a traversal that reaches the API end qualifies for absence comparisons. Normal runs retain all last-observed jobs; counts are not currently-open vacancies. `resume` does not fetch from the network and is the preferred retry after a downstream failure or Jobicy hourly limit.

The CLI obtains a nonblocking OS lock at `data/state/pipeline.lock`. The lock is released on process exit, including crashes; the presence of the file alone does not indicate a running process. Standalone module writers bypass orchestration: do not run them in parallel. DuckDB may require stopping the dashboard before publishing Gold. Failed Silver quality prevents skills, Gold and snapshots from running. Gold's own integrity checks roll back its transaction. If failure occurs after Gold commit but before snapshot capture, Gold may be newer than the last snapshot; resume safely fills the snapshot.

## Run diagnostics

- `data/state/latest_run.json`: latest pipeline outcome, stages, UTC start/finish, durations, return codes, log paths and errors.
- `data/state/runs/<run-id>.json` and adjacent stage `.log` files: durable run history and record counters.
- `data/state/quality/<run-id>/{silver,gold,snapshots}.json`: named critical checks, advisory warnings and measured details. Invalid data produces a report then a nonzero exit code.
- `data/state/jobs.sqlite`: ingestion manifests, page checkpoints, changed IDs/versions, full job history and lifecycle events.
- Bronze `manifest.json`: reviewable export of each source run, including page cap and pagination completion.

If the machine is killed before a final status write, `running` can remain in JSON. Check process/lock state, then resume; do not interpret it as success. Fetch freshness comes from the operational run/last_seen metadata. Job `ingested_at` remains the content observation timestamp when content is unchanged, so it is not a daily heartbeat.

## Daily macOS scheduling

```sh
python scripts/manage_schedule.py write --hour 9 --minute 0  # generate and lint only
python scripts/manage_schedule.py install --hour 9 --minute 0
python scripts/manage_schedule.py status
```

The user LaunchAgent runs both sources daily at 09:00 in the Mac system timezone (Europe/Berlin if configured that way), using this checkout's interpreter and absolute working directory. Installing does not immediately fetch jobs. Paths with spaces are safely represented as plist arrays. Logs: `data/state/launchd/{stdout,stderr}.log`. A generated plist is stored in `~/Library/LaunchAgents/com.danishibin.european-job-market-pipeline.plist`.

The user must be logged in. launchd does not wake a powered-off Mac. Calendar jobs missed during sleep normally run on wake, with multiple missed intervals coalesced; missed days do not create backdated snapshots. Offline runs fail with bounded HTTP retries and save diagnostics. There is no guaranteed catch-up after shutdown/logout and no paid always-on infrastructure. Run manually or resume when available. The schedule follows system daylight saving changes; snapshot dates remain UTC. Moving this checkout or replacing `.venv` requires reinstalling the plist.

To trigger the actual scheduled command manually: `launchctl kickstart gui/$(id -u)/com.danishibin.european-job-market-pipeline`. This makes live API requests; respect Jobicy's hourly limit. Inspect `status` and the stage logs afterward. To stop scheduling without deleting data: `launchctl bootout gui/$(id -u)/com.danishibin.european-job-market-pipeline`.

## Failure guide

| Failure | Next action |
| --- | --- |
| HTTP 429 / timeout / 5xx | GET retries use bounded exponential backoff and Retry-After. Inspect stage log; retry later. Use resume for already saved pages. |
| Jobicy hourly polling limit | Use resume; wait at least an hour for a fresh API pass. |
| API schema / timestamp error | Raw accepted pages remain; do not coerce bad fields silently. Update adapter and offline contract tests. |
| Disk failure during Bronze/state commit | Resume replays saved uncommitted pages exactly once. Temporary incomplete JSON files are ignored. |
| Disk failure during Silver/skill export | Durable dirty markers cause the next resume to retry exports even when no new pages exist. |
| Critical quality failure | Read the named checks and correct the upstream record/adapter; do not bypass validation. |
| DuckDB locked | Stop competing reader/writer processes and resume. |
| Snapshot failure | Existing dates remain intact; resume re-captures the current UTC day transactionally. |
| Pipeline already running | Inspect the active process. Do not remove a lock to permit simultaneous writers. |

## Backups and storage

Back up the entire `data` tree while writers are stopped. Bronze is immutable; never edit an already checkpointed page in place. Silver processing.sqlite is the persistent upsert store; Parquet files are query/export artifacts. Gold can be recreated from Silver but snapshots and operational/job history cannot be reconstructed from the current Gold alone. Back up snapshots.sqlite separately as part of the same data backup. Data grows locally; retention and log rotation remain manual and must preserve recovery inputs.

## Containers

```sh
docker compose build
# First population (network; daily source rules apply):
docker compose run --rm pipeline
# Run UI using the shared persistent named volume:
docker compose up -d dashboard
# Tests use temporary data and do not mount the real data volume:
docker compose run --build --rm tests
# Reprocess stored data without fetching:
docker compose run --rm pipeline python -m src.job_market.cli resume
```

Open http://localhost:8501. The dashboard mount is read-only and the port binds to loopback. Stop it while writing Gold if DuckDB reports a lock. `docker compose down` keeps data; do not use `down -v` unless deliberately deleting the volume. Container storage is separate from the host `data/` tree. The Jobicy hourly guard is per state database: choose one live collector (host schedule or containers), and do not use independent volumes to bypass the polling interval. The Streamlit health check indicates HTTP readiness, not data freshness; inspect the in-app run status for pipeline outcome. Never run host and container schedulers against the same storage concurrently.
