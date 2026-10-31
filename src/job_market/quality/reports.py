"""Machine-readable reports with explicit critical and advisory checks."""
from datetime import datetime, timezone
import os
import json
from pathlib import Path
import uuid

from src.job_market.storage.files import atomic_json


def publish_report(stage, checks, warnings=None, details=None):
    run_id = os.environ.get('JOB_MARKET_RUN_ID') or str(uuid.uuid4())
    report = dict(run_id=run_id, stage=stage, captured_at=datetime.now(timezone.utc).isoformat(),
                  passed=all(checks.values()), checks=checks, warnings=warnings or [], details=details or {})
    atomic_json(Path('data/state/quality') / run_id / f'{stage}.json', report)
    for name, passed in checks.items():
        print(f'{name}: {"PASS" if passed else "FAIL"}')
    print(json.dumps(dict(event='quality_report', run_id=run_id, stage=stage, passed=report['passed'], details=report['details'])))
    if not report['passed']:
        raise ValueError(f'{stage} data quality validation failed: {[name for name, passed in checks.items() if not passed]}')
    return report
