import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests

API_URL = "https://www.arbeitnow.com/api/job-board-api"
BRONZE_DIR = Path("data/bronze/arbeitnow")


def ingest_jobs(max_pages: int = 2) -> int:
    run_id = str(uuid.uuid4())
    ingested_at = datetime.now(timezone.utc)
    run_date = ingested_at.strftime("%Y-%m-%d")

    output_dir = BRONZE_DIR / f"date={run_date}" / f"run={run_id}"
    output_dir.mkdir(parents=True, exist_ok=True)

    total_jobs = 0

    with requests.Session() as session:
        for page in range(1, max_pages + 1):
            response = session.get(
                API_URL,
                params={"page": page},
                timeout=30,
                headers={"User-Agent": "JobMarketPortfolio/0.1"},
            )
            response.raise_for_status()

            payload = response.json()
            jobs = payload.get("data", [])

            if not isinstance(jobs, list):
                raise ValueError("Unexpected API response schema")

            record = {
                "source": "arbeitnow",
                "run_id": run_id,
                "ingested_at": ingested_at.isoformat(),
                "page": page,
                "payload": payload,
            }

            output_path = output_dir / f"page_{page}.json"
            output_path.write_text(
                json.dumps(record, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            total_jobs += len(jobs)
            print(f"Page {page}: {len(jobs)} jobs saved")

            if not jobs or not payload.get("links", {}).get("next"):
                break

            if page < max_pages:
                time.sleep(2)

    print(f"Run {run_id}: {total_jobs} records ingested")
    return total_jobs


if __name__ == "__main__":
    ingest_jobs()
