output "workload_identity_provider" {
  description = "Full resource name for the workflow's workload_identity_provider input"
  value       = google_iam_workload_identity_pool_provider.github.name
}

output "service_account_email" {
  description = "Full resource name for the workflow's service_account input"
  value       = google_service_account.ci.email
}

output "project_id" {
  value = var.project_id
}
