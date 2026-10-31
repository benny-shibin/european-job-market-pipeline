"""Local orchestration with exclusive writer lock and durable run diagnostics."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

from src.job_market.storage.files import atomic_json

PIPELINE_STEPS = [
    ('Incremental Ingestion', 'src.job_market.ingestion.incremental'),
    ('Bronze to Silver', 'src.job_market.transformations.bronze_2_silver'),
    ('Silver Data Quality', 'src.job_market.quality.validators'),
    ('Skill Extraction', 'src.job_market.transformations.extract_skills'),
    ('Silver to Gold', 'src.job_market.transformations.silver_2_gold'),
    ('Daily Market Snapshots', 'src.job_market.analytics.daily_snapshots'),
]
STATE_DIR = Path('data/state')


def utc_now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def pipeline_lock():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with (STATE_DIR / 'pipeline.lock').open('a+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError('Another pipeline writer holds data/state/pipeline.lock') from exc
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def run_pipeline(sources=('arbeitnow',), max_pages=2, resume=False):
    with pipeline_lock():
        run_id = str(uuid.uuid4())
        status = dict(run_id=run_id, started_at=utc_now(), status='running', sources=list(sources), stages=[])
        status_path = STATE_DIR / 'runs' / f'{run_id}.json'

        def save():
            atomic_json(status_path, status)
            atomic_json(STATE_DIR / 'latest_run.json', status)

        save()
        started = time.perf_counter()
        env = os.environ | {'JOB_MARKET_RUN_ID': run_id}
        try:
            for name, module in PIPELINE_STEPS:
                commands = [[]]
                if module.endswith('.incremental'):
                    commands = [['--recover-only']] if resume else [['--source', source, '--max-pages', str(max_pages)] for source in sources]
                for arguments in commands:
                    stage = dict(name=name, module=module, arguments=arguments, started_at=utc_now(), status='running')
                    status['stages'].append(stage)
                    save()
                    print(json.dumps(dict(event='stage_started', run_id=run_id, **stage)), flush=True)
                    step_started = time.perf_counter()
                    log_path = STATE_DIR / 'runs' / f'{run_id}-{len(status["stages"])}.log'
                    # Keep full child output for actionable failures and row counters.
                    with log_path.open('w') as log:
                        result = subprocess.run([sys.executable, '-m', module, *arguments], env=env,
                                                stdout=log, stderr=subprocess.STDOUT, check=False)
                    output = log_path.read_text()
                    print(output, end='', flush=True)
                    stage['metrics'] = []
                    for line in output.splitlines():
                        try:
                            record = json.loads(line)
                        except ValueError:
                            continue
                        if isinstance(record, dict) and record.get('event') in {'stage_metrics', 'quality_report'}:
                            stage['metrics'].append(record)
                    stage.update(status='complete' if result.returncode == 0 else 'failed',
                                 exit_code=result.returncode, duration_seconds=round(time.perf_counter() - step_started, 3),
                                 finished_at=utc_now(), log_path=str(log_path))
                    save()
                    print(json.dumps(dict(event='stage_finished', run_id=run_id, **stage)), flush=True)
                    if result.returncode:
                        raise RuntimeError(f'{name} failed (exit code {result.returncode}); see {log_path}')
            status['status'] = 'complete'
        except BaseException as exc:
            status.update(status='failed', error=str(exc))
            raise
        finally:
            status.update(finished_at=utc_now(), duration_seconds=round(time.perf_counter() - started, 3))
            save()
        return status


def main():
    parser = argparse.ArgumentParser(description='European Job Market Data Pipeline')
    parser.add_argument('command', choices=['run', 'resume'])
    parser.add_argument('--sources', nargs='+', choices=['arbeitnow', 'jobicy'], default=['arbeitnow'])
    parser.add_argument('--max-pages', type=int, default=2)
    args = parser.parse_args()
    if args.max_pages < 1:
        parser.error('--max-pages must be positive')
    try:
        run_pipeline(tuple(dict.fromkeys(args.sources)), args.max_pages, resume=args.command == 'resume')
    except (RuntimeError, OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == '__main__':
    main()
