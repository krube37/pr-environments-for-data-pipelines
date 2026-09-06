variable "project_id" {
  description = "GCP project the demo runs in"
  type        = string
}

variable "bq_location" {
  description = "BigQuery dataset location"
  type        = string
  default     = "US"
}

variable "github_repo" {
  description = "GitHub repo allowed to assume the CI service account, as \"owner/repo\""
  type        = string
  default     = "krube37/pr-environments-for-data-pipelines"
}
