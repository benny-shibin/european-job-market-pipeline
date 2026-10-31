
import re
from pathlib import Path

import polars as pl
import yaml

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
    df = pl.read_parquet(SILVER_PATH)
    skills = load_skills()

    records = []

    for job in df.iter_rows(named=True):
        description = job.get("description") or ""
        title = job.get("title") or ""
        tags = job.get("tags") or []

        combined_text = " ".join(
            [title, description, *map(str, tags)]
        )

        detected = extract_skills(combined_text, skills)

        for skill in detected:
            records.append({
                "source": job["source"],
                "job_id": job["job_id"],
                "skill_name": skill,
            })

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    schema = {
        "source": pl.String,
        "job_id": pl.String,
        "skill_name": pl.String,
    }

    result = pl.DataFrame(records, schema=schema)

    result.write_parquet(OUTPUT_PATH)

    print(f"Processed {df.height} jobs")
    print(f"Extracted {result.height} job-skill relationships")


if __name__ == "__main__":
    build_job_skills()
