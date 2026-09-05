locals {
  notifications_job_name = coalesce(var.notifications_job_name, "${var.project_name}-${var.environment}-notifications")
}
