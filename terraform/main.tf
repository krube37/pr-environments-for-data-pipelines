data "google_project" "this" {
  project_id = var.project_id
}

resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = "github-pool"
  display_name              = "GitHub Actions"
}

resource "google_iam_workload_identity_pool_provider" "github" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github-provider"
  display_name                       = "GitHub OIDC"

  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
  }

  # Scopes the whole pool to this specific list of repos, not the GitHub
  # org — the attribute the interviewer will ask about first.
  attribute_condition = "assertion.repository in ${jsonencode(var.github_repos)}"

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account" "ci" {
  account_id   = "pr-data-diff-ci"
  display_name = "PR data-diff CI"
}

# One binding per trusted repo — the provider's attribute_condition above
# controls who CAN present a token at all, this controls who that token
# lets impersonate. Each repo maps to its own principalSet identity.
resource "google_service_account_iam_member" "wif_impersonation" {
  for_each           = toset(var.github_repos)
  service_account_id = google_service_account.ci.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${each.value}"
}

# Unconditioned on purpose: creating pr_<N> is a project-level permission,
# since the dataset doesn't exist yet to condition a resource name on.
resource "google_project_iam_member" "bq_user" {
  project = var.project_id
  role    = "roles/bigquery.user"
  member  = "serviceAccount:${google_service_account.ci.email}"
}

# Everything after creation IS conditioned: dataOwner (not project-wide
# BigQuery Admin) only on datasets named pr_*. Needs to be dataOwner, not
# dataEditor, because the CI job deletes-and-recreates the PR dataset on
# every push to keep re-runs idempotent, and only dataOwner grants
# bigquery.datasets.delete.
resource "google_project_iam_member" "bq_pr_dataset_owner" {
  project = var.project_id
  role    = "roles/bigquery.dataOwner"
  member  = "serviceAccount:${google_service_account.ci.email}"

  condition {
    title      = "pr-datasets-only"
    expression = "resource.name.startsWith(\"projects/${var.project_id}/datasets/pr_\")"
  }
}

resource "google_bigquery_dataset" "prod" {
  dataset_id = "prod"
  location   = var.bq_location
}

# Narrow read grant so the CI SA can clone FROM prod — the conditioned
# dataOwner role above deliberately does not match "prod".
resource "google_bigquery_dataset_iam_member" "prod_reader" {
  dataset_id = google_bigquery_dataset.prod.dataset_id
  role       = "roles/bigquery.dataViewer"
  member     = "serviceAccount:${google_service_account.ci.email}"
}

resource "google_bigquery_table" "orders" {
  dataset_id          = google_bigquery_dataset.prod.dataset_id
  table_id            = "orders"
  deletion_protection = false

  schema = jsonencode([
    { name = "order_id", type = "STRING", mode = "REQUIRED" },
    { name = "customer_id", type = "STRING", mode = "REQUIRED" },
    { name = "order_date", type = "TIMESTAMP", mode = "REQUIRED" },
    { name = "total_amount", type = "STRING", mode = "REQUIRED" },
    { name = "currency", type = "STRING", mode = "REQUIRED" },
    { name = "status", type = "STRING", mode = "REQUIRED" },
  ])
}

resource "google_bigquery_table" "customers" {
  dataset_id          = google_bigquery_dataset.prod.dataset_id
  table_id            = "customers"
  deletion_protection = false

  schema = jsonencode([
    { name = "customer_id", type = "STRING", mode = "REQUIRED" },
    { name = "name", type = "STRING", mode = "NULLABLE" },
    { name = "email", type = "STRING", mode = "NULLABLE" },
    { name = "segment", type = "STRING", mode = "REQUIRED" },
    { name = "signup_date", type = "DATE", mode = "NULLABLE" },
  ])
}
