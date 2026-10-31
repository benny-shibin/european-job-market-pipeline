"""Generate isolated synthetic data without making API calls or touching real data."""
import argparse
import os
from pathlib import Path
import shutil

from src.job_market.analytics.daily_snapshots import create_daily_snapshot
from src.job_market.storage.files import atomic_json
from src.job_market.transformations.bronze_2_silver import build_silver
from src.job_market.transformations.extract_skills import build_job_skills
from src.job_market.transformations.silver_2_gold import build_gold
from src.job_market.quality.validators import validate_silver

ROOT = Path(__file__).resolve().parents[1]


def create_demo(output):
    output = Path(output).resolve()
    if output.exists():
        raise ValueError('Demo output must be a new directory; existing data is never overwritten')
    output.mkdir(parents=True)
    shutil.copytree(ROOT / 'config', output / 'config')
    previous = Path.cwd()
    try:
        os.chdir(output)
        atomic_json(Path('data/demo.json'), dict(synthetic=True, description='Generated examples, not live job observations'))
        for day, count in [('2026-10-29', 5), ('2026-10-30', 8), ('2026-10-31', 12)]:
            jobs = []
            for index in range(count):
                jobs.append(dict(slug=f'demo-{index}', title=['Python Engineer', 'SQL Analyst', 'Platform Engineer'][index % 3],
                    company_name=['Demo Analytics', 'Example Cloud', 'Sample Systems'][index % 3],
                    location=['Berlin', 'Paris', 'Europe'][index % 3], remote=index % 2 == 0,
                    url=f'https://example.com/demo/{index}',
                    description=['Python SQL AWS Docker', 'SQL Azure Power BI', 'Kubernetes Terraform AWS'][index % 3],
                    created_at=1793232000 + index * 3600, tags=[]))
            atomic_json(Path(f'data/bronze/arbeitnow/date={day}/run=demo-{day}/page_1.json'),
                        dict(source='arbeitnow', run_id=f'demo-{day}', page=1, ingested_at=f'{day}T12:00:00+00:00',
                             payload=dict(data=jobs, links=dict(next=None))))
            build_silver()
            validate_silver()
            build_job_skills()
            build_gold()
            create_daily_snapshot(day)
    finally:
        os.chdir(previous)
    return output / 'data'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(f'Demo data directory: {create_demo(args.output)}')
