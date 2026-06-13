locals {
  media_storage_account_name = coalesce(var.media_storage_account_name, "${replace(var.project_name, "-", "")}${var.environment}media")
}
