
import json
from pathlib import Path

import polars as pl

BRONZE_DIR = Path("data/bronze/arbeitnow")
SILVER_DIR = Path("data/silver/arbeitnow")


def build_silver() -> None:
    records = []

    for path in BRONZE_DIR.rglob("page_*.json"):
        raw = json.loads(path.read_text(encoding="utf-8"))

        for job in raw["payload"]["data"]:
            records.append({
                "source": raw["source"],
                "job_id": str(job.get("slug") or job.get("url") or ""),
                "title": job.get("title"),
                "company": job.get("company_name"),
                "location": job.get("location"),
                "remote": job.get("remote"),
                "url": job.get("url"),
                "ingested_at": raw["ingested_at"],
                "description": job.get("description"),
                "created_at": job.get("created_at"),
                "tags": job.get("tags", []),
            })

    if not records:
        print("No Bronze records available")
        return

    df = (
        pl.DataFrame(records)
        .filter(pl.col("job_id") != "")
        .with_columns(
    pl.col("ingested_at").str.to_datetime(time_zone="UTC")
)
        .sort("ingested_at")
        .unique(subset=["source", "job_id"], keep="last")
    )

    SILVER_DIR.mkdir(parents=True, exist_ok=True)
    df.write_parquet(SILVER_DIR / "jobs.parquet")

    print(f"Silver layer created: {df.height} unique jobs")


if __name__ == "__main__":
    build_silver()
