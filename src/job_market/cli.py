
import argparse
import subprocess
import sys
import time


PIPELINE_STEPS = [
    (
        "Incremental Ingestion",
        "src.job_market.ingestion.incremental",
    ),
    (
        "Bronze to Silver",
        "src.job_market.transformations.bronze_2_silver",
    ),
    (
        "Silver Data Quality",
        "src.job_market.quality.validators",
    ),
    
    (
        "Skill Extraction",
        "src.job_market.transformations.extract_skills",
    ),
    (
        "Silver to Gold",
        "src.job_market.transformations.silver_2_gold",
    ),
    (
        "Daily Market Snapshots",
        "src.job_market.analytics.daily_snapshots",
    ),
]


def run_pipeline():
    print("\nEUROPEAN JOB MARKET DATA PIPELINE")
    print("=" * 50)

    start = time.perf_counter()

    for step_name, module in PIPELINE_STEPS:
        print(f"\nRunning: {step_name}", flush=True)
        print("-" * 50, flush=True)

        step_start = time.perf_counter()

        result = subprocess.run(
            [sys.executable, "-m", module],
            check=False,
        )

        if result.returncode != 0:
            print(
                f"\nFAILED: {step_name} "
                f"(exit code {result.returncode})"
            )
            sys.exit(result.returncode)

        duration = time.perf_counter() - step_start

        print(
            f"COMPLETED: {step_name} "
            f"in {duration:.2f} seconds",
            flush=True,
        )

    total_duration = time.perf_counter() - start

    print("\n" + "=" * 50)
    print("PIPELINE COMPLETED SUCCESSFULLY")
    print(f"Total duration: {total_duration:.2f} seconds")


def main():
    parser = argparse.ArgumentParser(
        description="European Job Market Data Pipeline"
    )

    parser.add_argument(
        "command",
        choices=["run"],
        help="Pipeline command",
    )

    args = parser.parse_args()

    if args.command == "run":
        run_pipeline()


if __name__ == "__main__":
    main()
