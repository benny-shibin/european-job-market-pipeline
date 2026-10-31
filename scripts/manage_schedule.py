#!/usr/bin/env python3
"""Install/inspect a daily user launchd job; never run the pipeline on load."""
import argparse
from datetime import datetime
from pathlib import Path
import plistlib
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
LABEL = 'com.danishibin.european-job-market-pipeline'


def make_plist(root, python, hour=9, minute=0):
    logs = root / 'data/state/launchd'
    return dict(Label=LABEL, ProgramArguments=[str(python), '-m', 'src.job_market.cli', 'run',
                '--sources', 'arbeitnow', 'jobicy'], WorkingDirectory=str(root),
                StartCalendarInterval=dict(Hour=hour, Minute=minute), RunAtLoad=False,
                ProcessType='Background', EnvironmentVariables=dict(PYTHONUNBUFFERED='1'),
                StandardOutPath=str(logs / 'stdout.log'), StandardErrorPath=str(logs / 'stderr.log'))


def main():
    import os
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['write', 'install', 'status'])
    parser.add_argument('--hour', type=int, default=9)
    parser.add_argument('--minute', type=int, default=0)
    args = parser.parse_args()
    if not 0 <= args.hour <= 23 or not 0 <= args.minute <= 59:
        parser.error('Invalid clock time')
    if sys.platform != 'darwin':
        parser.error('launchd scheduling requires macOS; use Docker/manual execution elsewhere')
    path = Path.home() / 'Library/LaunchAgents' / f'{LABEL}.plist'
    domain = f'gui/{os.getuid()}'
    if args.action == 'status':
        result = subprocess.run(['launchctl', 'print', f'{domain}/{LABEL}'], check=False)
        raise SystemExit(result.returncode)
    (ROOT / 'data/state/launchd').mkdir(parents=True, exist_ok=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = plistlib.dumps(make_plist(ROOT, Path(sys.executable).absolute(), args.hour, args.minute))
    temporary = path.with_suffix('.tmp')
    temporary.write_bytes(data)
    temporary.replace(path)
    subprocess.run(['plutil', '-lint', str(path)], check=True)
    print(f'Wrote {path}: daily {args.hour:02d}:{args.minute:02d} in the Mac system timezone')
    if args.action == 'install':
        subprocess.run(['launchctl', 'bootout', f'{domain}/{LABEL}'], check=False, capture_output=True)
        subprocess.run(['launchctl', 'bootstrap', domain, str(path)], check=True)
        print('Loaded schedule. RunAtLoad=false; installation does not fetch data.')


if __name__ == '__main__':
    main()
