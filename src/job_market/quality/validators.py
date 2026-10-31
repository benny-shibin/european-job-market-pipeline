
from pathlib import Path
import polars as pl

SILVER_PATH = Path("data/silver/arbeitnow/jobs.parquet")


def validate_silver():
    df = pl.read_parquet(SILVER_PATH)

    checks = {
        "dataset_not_empty": df.height > 0,
        "job_id_not_null": df["job_id"].null_count() == 0,
        "job_id_not_blank": (
            df["job_id"].str.strip_chars() != ""
        ).all(),
        "unique_job_ids": (
            df.select(["source", "job_id"]).unique().height
            == df.height
        ),
        "title_not_null": df["title"].null_count() == 0,
        "company_not_null": df["company"].null_count() == 0,
        "ingested_at_not_null": (
            df["ingested_at"].null_count() == 0
        ),
    }

    print("\nDATA QUALITY REPORT")
    print("-" * 40)

    for check, passed in checks.items():
        status = "PASS" if passed else "FAIL"
        print(f"{check}: {status}")

    if not all(checks.values()):
        raise ValueError("Silver data quality validation failed")

    print("\nAll data quality checks passed!")


if __name__ == "__main__":
    validate_silver()
