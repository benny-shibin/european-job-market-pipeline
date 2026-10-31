"""Read-only provenance and row-integrity audit for configured public sources."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

import duckdb
import polars as pl

from src.job_market.ingestion.sources import parse_payload

RESTRICTED_SOURCES = {
    'linkedin': dict(name='LinkedIn', status='access_required',
        reason='No documented free public jobs-search feed. Approved Talent Solutions APIs require partner access and serve posting/integration use cases.',
        documentation='https://learn.microsoft.com/en-us/linkedin/talent/job-postings/api/overview'),
    'stepstone': dict(name='StepStone', status='access_required',
        reason='Published JobFeed interfaces publish employer listings to StepStone. No free public jobs-search feed was identified in official documentation.',
        documentation='https://api.stepstone.com/knowledge-base/xml-guide-stepstone-de/'),
}


def audit_sources(data_dir=Path('data')):
    data_dir = Path(data_dir).resolve()
    result = dict(checked_at=datetime.now(timezone.utc).isoformat(), sources=[])
    gold_path = data_dir / 'gold/job_market.duckdb'
    if not gold_path.exists():
        return result
    with duckdb.connect(str(gold_path), read_only=True) as gold:
        gold_rows = gold.execute('SELECT source, job_id FROM fact_job_postings').fetchall()
        projections = {(row[0], row[1]): row[2:] for row in gold.execute('SELECT source, job_id, title, remote, url FROM fact_job_postings').fetchall()}
        for source in ['arbeitnow', 'jobicy']:
            gold_ids = {job_id for name, job_id in gold_rows if name == source}
            row = dict(source=source, name={'arbeitnow': 'Arbeitnow', 'jobicy': 'Jobicy'}[source],
                       status='not_ingested', gold_jobs=len(gold_ids), checks={})
            path = data_dir / 'silver' / source / 'jobs.parquet'
            if path.exists():
                frame = pl.read_parquet(path)
                silver_ids = set(frame.filter(pl.col('source') == source)['job_id'].to_list())
                row['silver_jobs'] = len(silver_ids)
                row['checks']['fetch_provenance_available'] = False
                row['checks']['silver_gold_ids_match'] = silver_ids == gold_ids
                silver_projections = {(item['source'], item['job_id']): (item['title'], item['remote'], item['url']) for item in frame.iter_rows(named=True)}
                row['checks']['silver_gold_content_matches'] = all(projections.get(key) == values for key, values in silver_projections.items())
                row['checks']['unique_silver_jobs'] = frame.select('source', 'job_id').unique().height == frame.height
                state = data_dir / 'state/jobs.sqlite'
                if state.exists():
                    with sqlite3.connect(f'{state.as_uri()}?mode=ro', uri=True) as con:
                        run = con.execute('''SELECT run_id, status, started_at, pages, pagination_complete, counts_json
                            FROM ingestion_runs WHERE source=? ORDER BY started_at DESC LIMIT 1''', (source,)).fetchone()
                        if run:
                            row['checks']['fetch_provenance_available'] = True
                            row['latest_run'] = dict(zip(['run_id', 'status', 'started_at', 'pages', 'pagination_complete', 'counts'], run))
                            row['latest_run']['counts'] = json.loads(row['latest_run']['counts'])
                            row['checks']['latest_fetch_succeeded'] = run[1] == 'complete'
                            ids = {item[0] for item in con.execute('SELECT job_id FROM run_observations WHERE run_id=?', (run[0],))}
                            row['latest_observed_jobs'] = len(ids)
                            row['checks']['latest_observations_in_gold'] = ids <= gold_ids
                            fetched = 0
                            saved_pages = list((data_dir / 'bronze' / source).glob(f'date=*/run={run[0]}/page_*.json'))
                            for page in saved_pages:
                                raw = json.loads(page.read_text())
                                jobs, _ = parse_payload(source, raw['payload'])
                                fetched += len(jobs)
                            row['checks']['saved_page_count_matches'] = len(saved_pages) == run[3]
                            row['checks']['bronze_classification_counts_match'] = fetched == sum(row['latest_run']['counts'].values())
                            row['latest_fetched_records'] = fetched
                            state_rows = dict(con.execute('SELECT job_id, content_hash FROM job_state WHERE source=?', (source,)))
                            silver_hashes = dict(frame.select('job_id', 'content_hash').iter_rows()) if 'content_hash' in frame.columns else {}
                            row['checks']['state_hashes_match_silver'] = bool(state_rows) and all(silver_hashes.get(key) == value for key, value in state_rows.items())
                row['status'] = 'verified' if gold_ids and all(row['checks'].values()) else 'needs_attention'
            elif gold_ids:
                row['status'] = 'needs_attention'
                row['checks']['silver_dataset_available'] = False
            result['sources'].append(row)
    result['sources'].extend(dict(source=key, gold_jobs=0, checks={}, **value) for key, value in RESTRICTED_SOURCES.items())
    return result


if __name__ == '__main__':
    print(json.dumps(audit_sources(), indent=2))
