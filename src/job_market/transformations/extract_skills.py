
import re
import hashlib
import json
import sqlite3
from pathlib import Path

import polars as pl
import yaml

from src.job_market.storage.files import atomic_parquet

SILVER_PATH = Path("data/silver/arbeitnow/jobs.parquet")
SKILLS_PATH = Path("config/skills.yaml")
OUTPUT_PATH = Path("data/silver/arbeitnow/job_skills.parquet")


def load_skills():
    with SKILLS_PATH.open(encoding="utf-8") as file:
        return yaml.safe_load(file)["skills"]


def extract_skills(text, skill_dictionary):
    if not text:
        return []

    matches = set()

    for skill, keywords in skill_dictionary.items():
        for keyword in keywords:
            pattern = (
                r"(?<!\w)"
                + re.escape(keyword)
                + r"(?!\w)"
            )

            if re.search(pattern, text, flags=re.IGNORECASE):
                matches.add(skill)
                break

    return sorted(matches)


def build_job_skills():
    dictionary = load_skills()
    dictionary_hash = hashlib.sha256(json.dumps(dictionary, sort_keys=True).encode()).hexdigest()
    total_processed = 0
    for path in sorted(SILVER_PATH.parent.parent.glob('*/jobs.parquet')):
        output = path.with_name('job_skills.parquet')
        with sqlite3.connect(path.with_name('skills.sqlite')) as con:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS jobs (source TEXT, job_id TEXT, fingerprint TEXT,
                    PRIMARY KEY (source, job_id));
                CREATE TABLE IF NOT EXISTS skills (source TEXT, job_id TEXT, skill_name TEXT,
                    PRIMARY KEY (source, job_id, skill_name));
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT);
            """)
            stat = path.stat()
            input_signature = f'{stat.st_mtime_ns}:{stat.st_size}:{dictionary_hash}'
            dirty = con.execute("SELECT 1 FROM metadata WHERE key='dirty'").fetchone()
            previous = con.execute("SELECT value FROM metadata WHERE key='input_signature'").fetchone()
            if previous and previous[0] == input_signature and output.exists() and not dirty:
                continue
            processed = 0
            for job in pl.read_parquet(path).iter_rows(named=True):
                text = ' '.join([job.get('title') or '', job.get('description') or '',
                                 *map(str, job.get('tags') or [])])
                fingerprint = hashlib.sha256((dictionary_hash + text).encode()).hexdigest()
                if con.execute('SELECT 1 FROM jobs WHERE source=? AND job_id=? AND fingerprint=?',
                               (job['source'], job['job_id'], fingerprint)).fetchone():
                    continue
                with con:
                    con.execute('DELETE FROM skills WHERE source=? AND job_id=?', (job['source'], job['job_id']))
                    con.executemany('INSERT INTO skills VALUES (?, ?, ?)',
                                    [(job['source'], job['job_id'], skill) for skill in extract_skills(text, dictionary)])
                    con.execute('INSERT OR REPLACE INTO jobs VALUES (?, ?, ?)', (job['source'], job['job_id'], fingerprint))
                    con.execute("INSERT OR REPLACE INTO metadata VALUES ('dirty', 'yes')")
                processed += 1
            if not output.exists() or con.execute("SELECT 1 FROM metadata WHERE key='dirty'").fetchone():
                rows = con.execute('SELECT source, job_id, skill_name FROM skills ORDER BY source, job_id, skill_name').fetchall()
                result = pl.DataFrame(rows, schema={'source': pl.String, 'job_id': pl.String, 'skill_name': pl.String}, orient='row')
                atomic_parquet(result, output)
                con.execute("DELETE FROM metadata WHERE key='dirty'")
                con.commit()
            con.execute("INSERT OR REPLACE INTO metadata VALUES ('input_signature', ?)", (input_signature,))
            con.commit()
            total_processed += processed
    print(json.dumps(dict(event='stage_metrics', changed_jobs=total_processed)))
    print(f'Skills: {total_processed} changed jobs processed')
    return total_processed


if __name__ == '__main__':
    build_job_skills()
