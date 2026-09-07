variable "project_id" {
  description = "GCP project the demo runs in"
  type        = string
}

variable "bq_location" {
  description = "BigQuery dataset location"
  type        = string
  default     = "US"
}

variable "github_repos" {
  description = "GitHub repos allowed to assume the CI service account, each as \"owner/repo\""
  type        = list(string)
  default = [
    "krube37/pr-environments-for-data-pipelines",
    "radhagayathris-cmd/pr-environments-for-data-pipelines",
  ]
}
