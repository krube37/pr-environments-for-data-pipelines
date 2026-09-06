#!/usr/bin/env python3
"""One-time script: generate synthetic customers/orders and load them into
BigQuery. Not part of CI — run manually against prod once:

    python scripts/generate_seed_data.py --project <PROJECT_ID>
"""
import argparse
import json
import random
import tempfile
import uuid
from datetime import datetime, timedelta

from google.cloud import bigquery

SEGMENTS = ["consumer", "smb", "enterprise"]
STATUSES = ["completed", "pending", "refunded", "cancelled"]
NUM_CUSTOMERS = 20_000
NUM_ORDERS = 1_000_000


def random_date(start, end):
    delta = end - start
    return start + timedelta(seconds=random.randint(0, int(delta.total_seconds())))


def generate_customers():
    for _ in range(NUM_CUSTOMERS):
        customer_id = str(uuid.uuid4())
        yield {
            "customer_id": customer_id,
            "name": f"Customer {customer_id[:8]}",
            "email": f"{customer_id[:8]}@example.com",
            "segment": random.choices(SEGMENTS, weights=[70, 25, 5])[0],
            "signup_date": random_date(
                datetime(2022, 1, 1), datetime(2026, 1, 1)
            ).date().isoformat(),
        }


def generate_orders(customer_ids):
    start, end = datetime(2025, 1, 1), datetime(2026, 1, 1)
    for _ in range(NUM_ORDERS):
        amount = round(random.uniform(5, 500), 2)
        yield {
            "order_id": str(uuid.uuid4()),
            "customer_id": random.choice(customer_ids),
            "order_date": random_date(start, end).isoformat(),
            # Raw source data is messy on purpose — see orders.total_amount
            # in terraform/main.tf for why this is a string, not a number.
            "total_amount": f"{amount:.2f}",
            "currency": "GBP",
            "status": random.choices(STATUSES, weights=[85, 5, 7, 3])[0],
        }


def load(client, project, dataset, table, rows):
    with tempfile.NamedTemporaryFile(mode="w", suffix=".ndjson", delete=False) as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
        path = f.name

    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )
    table_ref = f"{project}.{dataset}.{table}"
    with open(path, "rb") as f:
        job = client.load_table_from_file(f, table_ref, job_config=job_config)
    job.result()
    print(f"Loaded {job.output_rows} rows into {table_ref}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--dataset", default="prod")
    args = parser.parse_args()

    client = bigquery.Client(project=args.project)

    customers = list(generate_customers())
    load(client, args.project, args.dataset, "customers", customers)

    customer_ids = [c["customer_id"] for c in customers]
    load(client, args.project, args.dataset, "orders", generate_orders(customer_ids))


if __name__ == "__main__":
    main()
