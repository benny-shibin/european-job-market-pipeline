from datetime import datetime, timezone
from pathlib import Path

import polars as pl

from src.job_market.quality.reports import publish_report

SILVER_PATH = Path('data/silver/arbeitnow/jobs.parquet')


def validate_silver():
    paths = sorted(SILVER_PATH.parent.parent.glob('*/jobs.parquet'))
    checks = {'datasets_present': bool(paths)}
    warnings = []
    details = {}
    for path in paths:
        df = pl.read_parquet(path)
        prefix = path.parent.name + '.'
        required = {'source', 'job_id', 'title', 'company', 'remote', 'ingested_at', 'url'}
        checks[prefix + 'required_columns'] = required <= set(df.columns)
        for name in required - set(df.columns):
            dtype = pl.Boolean if name == 'remote' else pl.Datetime('us', 'UTC') if name == 'ingested_at' else pl.String
            df = df.with_columns(pl.lit(None, dtype=dtype).alias(name))
        checks.update({prefix + name: bool(value) for name, value in {
            'dataset_not_empty': df.height > 0,
            'expected_row_count': df.height <= 10_000_000,
            'job_id_not_null': df['job_id'].null_count() == 0,
            'job_id_not_blank': df['job_id'].cast(pl.String).str.strip_chars().ne('').all(),
            'source_valid': df['source'].is_in(['arbeitnow', 'jobicy']).all() and df['source'].null_count() == 0,
            'source_matches_partition': df['source'].eq(path.parent.name).all(),
            'unique_job_ids': df.select(['source', 'job_id']).unique().height == df.height,
            'title_not_null': df['title'].null_count() == 0,
            'title_not_blank': df['title'].cast(pl.String).str.strip_chars().ne('').all(),
            'company_not_null': df['company'].null_count() == 0,
            'company_not_blank': df['company'].cast(pl.String).str.strip_chars().ne('').all(),
            'ingested_at_not_null': df['ingested_at'].null_count() == 0,
            'remote_boolean': df.schema['remote'] == pl.Boolean,
            'timestamp_type': isinstance(df.schema['ingested_at'], pl.Datetime) and df.schema['ingested_at'].time_zone == 'UTC',
            'url_valid': df['url'].cast(pl.String).str.contains(r'^https?://[^/\s]+').fill_null(False).all(),
        }.items()})
        if checks[prefix + 'timestamp_type'] and df.height:
            newest = df['ingested_at'].max()
            age = (datetime.now(timezone.utc) - newest).total_seconds() / 3600
            details[prefix + 'newest_observation_age_hours'] = age
            if age > 48:
                warnings.append(f'{path.parent.name}: newest content observation is over 48 hours old; check run status for fetch freshness')
        details[prefix + 'rows'] = df.height
    return publish_report('silver', checks, warnings, details)


if __name__ == '__main__':
    validate_silver()
