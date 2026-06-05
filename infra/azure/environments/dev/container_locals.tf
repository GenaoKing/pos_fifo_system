locals {
  container_apps_location         = coalesce(var.container_apps_location, var.location)
  acr_name                        = coalesce(var.acr_name, "${replace(var.project_name, "-", "")}${var.environment}acr")
  container_apps_environment_name = coalesce(var.container_apps_environment_name, "${var.project_name}-${var.environment}-aca-env")
  api_container_app_name          = coalesce(var.api_container_app_name, "${var.project_name}-${var.environment}-api")
  migrate_job_name                = coalesce(var.migrate_job_name, "${var.project_name}-${var.environment}-migrate")
  container_image                 = "${module.container_registry.login_server}/${var.container_image_repository}:${var.container_image_tag}"
  container_tags = {
    project     = var.project_name
    environment = var.environment
    managed_by  = "terraform"
  }
}
