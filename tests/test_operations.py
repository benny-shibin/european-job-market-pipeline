import json
from pathlib import Path
import plistlib
from types import SimpleNamespace

import pytest

from src.job_market import cli
from scripts.manage_schedule import make_plist


def test_cli_lock_rejects_overlap(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, 'STATE_DIR', tmp_path)
    with cli.pipeline_lock():
        with pytest.raises(RuntimeError, match='Another pipeline writer'):
            with cli.pipeline_lock():
                pass
    with cli.pipeline_lock():
        pass


def test_pipeline_logs_failure_and_resume(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, 'STATE_DIR', tmp_path)
    commands = []

    def run(arguments, **kwargs):
        commands.append(arguments)
        kwargs['stdout'].write('Processed 4 jobs\n')
        return SimpleNamespace(returncode=1 if len(commands) == 2 else 0)

    monkeypatch.setattr(cli.subprocess, 'run', run)
    with pytest.raises(RuntimeError, match='Bronze to Silver failed'):
        cli.run_pipeline()
    failed = json.loads((tmp_path / 'latest_run.json').read_text())
    assert failed['status'] == 'failed'
    assert failed['stages'][-1]['exit_code'] == 1
    assert Path(failed['stages'][-1]['log_path']).read_text() == 'Processed 4 jobs\n'
    monkeypatch.setattr(cli.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(returncode=0))
    completed = cli.run_pipeline(resume=True)
    assert completed['status'] == 'complete'
    assert completed['stages'][0]['arguments'] == ['--recover-only']
    assert len(list((tmp_path / 'runs').glob('*.json'))) == 2


def test_launchd_configuration_round_trip(tmp_path):
    data = make_plist(tmp_path, Path('/example/venv/bin/python'), 9, 15)
    parsed = plistlib.loads(plistlib.dumps(data))
    assert parsed['WorkingDirectory'] == str(tmp_path)
    assert parsed['ProgramArguments'][:4] == ['/example/venv/bin/python', '-m', 'src.job_market.cli', 'run']
    assert parsed['StartCalendarInterval'] == {'Hour': 9, 'Minute': 15}
    assert parsed['RunAtLoad'] is False


def test_reproducible_demo_is_isolated(tmp_path):
    from scripts.create_demo import create_demo
    import sqlite3
    original = Path.cwd()
    path = create_demo(tmp_path / 'demo')
    assert Path.cwd() == original
    assert json.loads((path / 'demo.json').read_text())['synthetic'] is True
    with sqlite3.connect(path / 'state/snapshots.sqlite') as con:
        assert con.execute('SELECT total_jobs FROM daily_market_snapshots ORDER BY snapshot_date').fetchall() == [(5,), (8,), (12,)]
    with pytest.raises(ValueError, match='new directory'):
        create_demo(tmp_path / 'demo')
