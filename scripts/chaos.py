#!/usr/bin/env python3
"""Chaos experiment: clone prod, corrupt it 4 ways, see what actually
catches each one. Not part of CI — run by hand:

    python scripts/chaos.py --project <PROJECT_ID>

Each case gets a fresh clone (previous corruptions don't carry over).
"Caught" means either a dbt test failed or diff.py flagged the model as
breaking — whichever happens first is what would have stopped a real PR.
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

from google.cloud import bigquery

DBT_DIR = Path(__file__).parent.parent / "dbt"
DBT_BIN = str(Path(sys.executable).parent / "dbt")
DIFF_SCRIPT = str(Path(__file__).parent / "diff.py")

CASES = [
    {
        "name": "Blank 5% of a NOT NULL business key",
        "expected": "caught by not_null",
        "setup": "ALTER TABLE `{p}.{d}.orders` ALTER COLUMN customer_id DROP NOT NULL",
        "corrupt": "UPDATE `{p}.{d}.orders` SET customer_id = NULL WHERE RAND() < 0.05",
    },
    {
        "name": "Duplicate 2% of rows",
        "expected": "caught by unique + row-count diff",
        "corrupt": "INSERT INTO `{p}.{d}.orders` SELECT * FROM `{p}.{d}.orders` WHERE RAND() < 0.02",
    },
    {
        "name": 'Emit a number as text ("1,000")',
        "expected": "expected to be missed",
        "corrupt": "UPDATE `{p}.{d}.orders` SET total_amount = '1,000' WHERE RAND() < 0.05",
    },
    {
        "name": "Switch currency GBP -> USD",
        "expected": "expected to be missed",
        "corrupt": "UPDATE `{p}.{d}.orders` SET currency = 'USD' WHERE RAND() < 0.05",
    },
]


def reset_clone(client, project, dataset, prod_dataset):
    client.delete_dataset(f"{project}.{dataset}", delete_contents=True, not_found_ok=True)
    client.create_dataset(bigquery.Dataset(f"{project}.{dataset}"))
    for table in ("orders", "customers"):
        client.query(f"CREATE TABLE `{project}.{dataset}.{table}` CLONE `{project}.{prod_dataset}.{table}`").result()


def run_dbt_build(project, dataset):
    env = {**os.environ, "GCP_PROJECT_ID": project, "BQ_DATASET": dataset}
    result = subprocess.run(
        [DBT_BIN, "build", "--project-dir", str(DBT_DIR), "--profiles-dir", str(DBT_DIR)],
        env=env, capture_output=True, text=True,
    )
    failed = [line.strip() for line in result.stdout.splitlines() if "FAIL" in line or "ERROR" in line]
    return result.returncode == 0, failed


def run_diff(project, dataset, prod_dataset):
    output_path = "/tmp/chaos_diff.md"
    result = subprocess.run(
        [sys.executable, DIFF_SCRIPT, "--project", project, "--pr-dataset", dataset,
         "--prod-dataset", prod_dataset, "--models", "revenue_by_customer_segment",
         "--output", output_path],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        return False, None
    body = Path(output_path).read_text() if Path(output_path).exists() else result.stderr
    reason = "table not found (model skipped)" if "TABLE NOT FOUND" in body else "schema/row-count diff"
    return True, reason


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--dataset", default="chaos")
    parser.add_argument("--prod-dataset", default="prod")
    args = parser.parse_args()

    client = bigquery.Client(project=args.project)
    rows = []

    for case in CASES:
        print(f"--- {case['name']} ---")
        reset_clone(client, args.project, args.dataset, args.prod_dataset)
        if "setup" in case:
            client.query(case["setup"].format(p=args.project, d=args.dataset)).result()
        client.query(case["corrupt"].format(p=args.project, d=args.dataset)).result()

        dbt_ok, failed_tests = run_dbt_build(args.project, args.dataset)
        diff_flagged, diff_reason = run_diff(args.project, args.dataset, args.prod_dataset)
        caught = (not dbt_ok) or diff_flagged

        parts = []
        if failed_tests:
            parts.append("; ".join(failed_tests))
        if diff_flagged:
            parts.append(f"diff.py: {diff_reason}")
        detail = "; ".join(parts) if parts else "nothing"
        rows.append((case["name"], case["expected"], "caught" if caught else "MISSED", detail))

    client.delete_dataset(f"{args.project}.{args.dataset}", delete_contents=True, not_found_ok=True)

    print("\n" + f"{'Case':<40} {'Expected':<32} {'Actual':<8} Caught by")
    for name, expected, actual, detail in rows:
        print(f"{name:<40} {expected:<32} {actual:<8} {detail}")


if __name__ == "__main__":
    main()
