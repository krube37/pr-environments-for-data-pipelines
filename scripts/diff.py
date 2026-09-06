#!/usr/bin/env python3
"""Compare each changed dbt model's output in a PR dataset against prod.

    python scripts/diff.py --project P --pr-dataset pr_1423 \
        --models revenue_by_customer_segment segment_order_share

Two checks, nothing else:
  - schema diff: dropped column or type change is breaking, added column isn't
  - row-count diff: breaking if the delta is bigger than ROW_COUNT_THRESHOLD

Writes markdown to --output and exits non-zero if anything is breaking.
"""
import argparse

from google.cloud import bigquery

ROW_COUNT_THRESHOLD = 0.05


def get_columns(client, project, dataset, table):
    query = f"""
        select column_name, data_type
        from `{project}.{dataset}`.INFORMATION_SCHEMA.COLUMNS
        where table_name = @table
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("table", "STRING", table)]
    )
    return {row.column_name: row.data_type for row in client.query(query, job_config=job_config)}


def get_row_count(client, project, dataset, table):
    query = f"select count(*) as n from `{project}.{dataset}.{table}`"
    return next(iter(client.query(query).result())).n


def diff_schema(pr_cols, prod_cols):
    lines, breaking = [], False
    for name, dtype in prod_cols.items():
        if name not in pr_cols:
            lines.append(f"✗ column `{name}` dropped")
            breaking = True
        elif pr_cols[name] != dtype:
            lines.append(f"✗ column `{name}` changed type: {dtype} -> {pr_cols[name]}")
            breaking = True
    for name, dtype in pr_cols.items():
        if name not in prod_cols:
            lines.append(f"✓ column `{name}` added ({dtype})")
    return lines, breaking


def diff_row_count(pr_count, prod_count):
    delta = (pr_count - prod_count) / prod_count if prod_count else 0.0
    breaking = abs(delta) > ROW_COUNT_THRESHOLD
    mark = "✗" if breaking else "✓"
    line = f"{mark} {pr_count:,} rows (Δ {delta:+.1%}, threshold {ROW_COUNT_THRESHOLD:.0%})"
    return line, breaking


def diff_table(client, project, pr_dataset, prod_dataset, table):
    pr_cols = get_columns(client, project, pr_dataset, table)
    prod_cols = get_columns(client, project, prod_dataset, table)
    schema_lines, schema_breaking = diff_schema(pr_cols, prod_cols)

    pr_count = get_row_count(client, project, pr_dataset, table)
    prod_count = get_row_count(client, project, prod_dataset, table)
    volume_line, volume_breaking = diff_row_count(pr_count, prod_count)

    breaking = schema_breaking or volume_breaking
    header = "⚠️ **Breaking change — check failed**" if breaking else "✅ **No breaking change**"

    lines = [header, "", f"`{table}` in `{pr_dataset}`", "", "```"]
    lines.append("SCHEMA")
    if schema_lines:
        lines.extend(f"  {line}" for line in schema_lines)
    else:
        lines.append("  ✓ no schema changes")
    lines.append("")
    lines.append("VOLUME")
    lines.append(f"  {volume_line}")
    lines.append("```")
    if breaking:
        lines.append("")
        lines.append("Override with the `data-diff:accept` label.")
    return "\n".join(lines), breaking


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--pr-dataset", required=True)
    parser.add_argument("--prod-dataset", default="prod")
    parser.add_argument("--models", nargs="+", required=True)
    parser.add_argument("--output", default="diff.md")
    args = parser.parse_args()

    client = bigquery.Client(project=args.project)

    sections, any_breaking = [], False
    for table in args.models:
        section, breaking = diff_table(client, args.project, args.pr_dataset, args.prod_dataset, table)
        sections.append(section)
        any_breaking = any_breaking or breaking

    with open(args.output, "w") as f:
        f.write("\n\n---\n\n".join(sections) + "\n")

    raise SystemExit(1 if any_breaking else 0)


if __name__ == "__main__":
    main()
