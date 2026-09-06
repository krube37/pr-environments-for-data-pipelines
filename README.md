# PR Environments for Data Pipelines

Every pull request that changes a dbt model gets its own cloned BigQuery
dataset, runs the changed models against it, diffs the output against
production, and posts the result as a PR comment.

## How it works

1. A PR touching `dbt/models/**` opens.
2. GitHub Actions authenticates to GCP with no key anywhere — a
   GitHub-issued OIDC token is exchanged for short-lived Google
   credentials via Workload Identity Federation.
3. The workflow creates `pr_<PR_NUMBER>`, a BigQuery dataset that expires
   itself in 48 hours, and zero-copy clones `prod.orders`/`prod.customers`
   into it.
4. `dbt build` runs against that dataset.
5. `scripts/diff.py` compares the PR's version of each changed model
   against the live `prod` table — schema and row count only.
6. The result is posted as a comment on the PR. The check fails on a
   breaking change unless the PR carries the `data-diff:accept` label.

## Repo layout

- `terraform/` — one-time GCP setup: Workload Identity Federation, the CI
  service account (scoped to `pr_*` datasets, not project-wide), and the
  `prod` dataset/tables.
- `dbt/` — 4 models (2 staging, 2 marts), 3 tests.
- `scripts/generate_seed_data.py` — one-time: loads ~1M synthetic rows.
- `scripts/diff.py` — the check that runs in CI.
- `scripts/chaos.py` — manual: corrupts data 4 ways, records what catches it.
- `.github/workflows/data-diff.yml` — wires it all together.

## One-time setup

1. Create a GCP project with billing enabled, then enable:
   `bigquery.googleapis.com`, `iam.googleapis.com`,
   `iamcredentials.googleapis.com`, `sts.googleapis.com`,
   `cloudresourcemanager.googleapis.com`.

2. Apply the Terraform:

   ```
   cd terraform
   terraform init
   terraform apply -var="project_id=<YOUR_PROJECT_ID>" -var="github_repo=<owner>/<repo>"
   ```

   Copy the three outputs (`project_id`, `workload_identity_provider`,
   `service_account_email`) into the `env:` block at the top of
   `.github/workflows/data-diff.yml`, replacing the values already there
   (this repo's workflow currently points at one specific demo project —
   fork it and those three lines are the only thing you need to change).
   None of the three are secret — that's the point of Workload Identity
   Federation — so they're committed directly in the workflow file
   instead of living in repo secrets.

3. Load seed data and build the baseline:

   ```
   python -m venv .venv && source .venv/bin/activate
   pip install -r scripts/requirements.txt
   python scripts/generate_seed_data.py --project <YOUR_PROJECT_ID>
   GCP_PROJECT_ID=<YOUR_PROJECT_ID> BQ_DATASET=prod \
     dbt build --project-dir dbt --profiles-dir dbt
   ```

   That last command matters: `prod`'s mart tables have to exist before
   any PR diff means anything. Re-run it after every merge to `main` —
   there's no "deploy to prod" workflow here, that's out of scope for
   this demo.

4. Create the override label once:

   ```
   gh label create "data-diff:accept" --description "Override a failed data-diff check" --color FBCA04
   ```

## Running the demo

```
git checkout -b demo/drop-a-column
# edit dbt/models/marts/revenue_by_customer_segment.sql, remove a column
git commit -am "Drop a column"
git push -u origin demo/drop-a-column
gh pr create --title "Drop a column" --body "Demo"
gh pr checks --watch
```

The check fails and a comment appears naming the dropped column. Add the
`data-diff:accept` label to the PR to see the override path — labeling a
PR is also a trigger, so the same workflow re-runs and passes.

## Running the chaos experiment

```
python scripts/chaos.py --project <YOUR_PROJECT_ID>
```

Clones prod, applies one corruption at a time, runs `dbt build`, and
records whether a test or the diff script caught it. Prints a results
table. Not part of CI — this is for the presentation, not the pipeline.

## Design decisions

- **Zero-copy clone, not a full copy.** `CREATE TABLE ... CLONE ...` is
  metadata-only at creation; storage is billed only where the clone
  diverges from prod. A full copy would cost linearly in the number of
  open PRs.
- **No teardown code.** The PR dataset's `default_table_expiration` does
  it — BigQuery deletes it after 48 hours on its own.
- **No service account key, anywhere.** Workload Identity Federation lets
  GitHub's own short-lived OIDC token stand in for one. The
  `attribute_condition` on the pool provider scopes it to this exact
  repo, not the GitHub org.
- **Two IAM bindings, not one.** `roles/bigquery.user` is unconditioned
  at the project level, because creating `pr_<N>` happens before that
  dataset name exists to write a condition against. Everything after
  creation goes through a second binding — `roles/bigquery.dataOwner`,
  restricted via an IAM condition to datasets named `pr_*` — plus one
  narrow, explicit `dataViewer` grant on `prod` so the CI service account
  can read it without the `pr_*`-scoped role ever reaching it. It's
  `dataOwner`, not the more obvious `dataEditor`, because the workflow
  deletes and recreates the PR dataset on every push, and only
  `dataOwner` grants `bigquery.datasets.delete`.
- **`orders.total_amount` is a STRING**, not a number, on the raw table.
  Real source data is messy; this is deliberately the seam where the
  "number as text" chaos case hides behind a `SAFE_CAST` that fails
  silently instead of erroring.
- **The required second downstream model reads only `segment` and
  `order_count` from `revenue_by_customer_segment`, never
  `total_amount`.** That's the column the demo PR drops — if the
  downstream model referenced it, the drop would hard-crash `dbt build`
  with a SQL error instead of letting the diff bot catch it gracefully.
- **Schema diff and row-count diff only — no distribution diff.** Null
  rates, cardinality, and min/max per column would catch a third class
  of bug (see below). Scoped out to keep this small; it's the first
  thing to add next.

## What the chaos experiment actually found

| Injection | Expected | Actual | Caught by |
|---|---|---|---|
| Blank 5% of a NOT NULL business key | caught by `not_null` | caught | `not_null` test |
| Duplicate 2% of rows | caught by `unique` + row-count diff | caught | `unique` test |
| Emit a number as text ("1,000") | expected to be missed | **MISSED** | nothing |
| Switch currency GBP → USD | expected to be missed | **MISSED** | nothing |

The two expected misses were confirmed for the expected reason —
`SAFE_CAST` swallows the bad string to `NULL` with no error, and nothing
downstream even reads the currency column.

The two expected catches revealed something not in the original plan:
`dbt build` doesn't just log a failed test and move on — it **skips every
model downstream** of the model that failed. So the mart table never got
rebuilt in either case, and the row-count diff never got a chance to
compare real numbers; it just found a missing table. It's the test doing
the catching, not "the test plus an independent volume check" as
originally assumed. Running this also surfaced a real bug: `diff.py`
crashed with an unhandled exception the first time it hit a table that
didn't exist, instead of reporting anything useful — fixed to detect a
missing PR-side table and report it cleanly.

## Known limitations

- If a PR's change breaks a dbt test rather than just changing a model's
  shape, the `dbt build` step itself fails and the job stops there — the
  diff bot's comment step never runs. The polished PR comment only
  happens for changes that build successfully but produce different
  output; a test failure surfaces as a raw failed step in the Actions log
  instead.
- Model-change detection is `git diff` on `dbt/models/**/*.sql`
  filenames, not dbt manifest state comparison (explicitly out of
  scope). Fine for a 4-model demo; wouldn't scale to a large project
  without more thought.
- A brand-new model with no `prod` counterpart yet would diff against an
  empty baseline, which isn't really meaningful — an untested edge case,
  not the scenario this demo targets.
- No cost ceiling on the clone itself. A PR touching a genuinely huge,
  fast-changing table could still run up real divergence costs; nothing
  here caps that.
