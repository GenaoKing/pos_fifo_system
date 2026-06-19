locals {
  container_apps_location         = coalesce(var.container_apps_location, var.location)
  use_existing_container_registry = var.existing_container_registry_id != null
  acr_name                        = coalesce(var.acr_name, "${replace(var.project_name, "-", "")}${var.environment}acr")
  container_registry_id           = local.use_existing_container_registry ? var.existing_container_registry_id : module.container_registry[0].id
  container_registry_name         = local.use_existing_container_registry ? coalesce(var.existing_container_registry_name, local.acr_name) : module.container_registry[0].name
  container_registry_login_server = local.use_existing_container_registry ? var.existing_container_registry_login_server : module.container_registry[0].login_server
  container_apps_environment_name = coalesce(var.container_apps_environment_name, "${var.project_name}-${var.environment}-aca-env")
  api_container_app_name          = coalesce(var.api_container_app_name, "${var.project_name}-${var.environment}-api")
  migrate_job_name                = coalesce(var.migrate_job_name, "${var.project_name}-${var.environment}-migrate")
  container_image                 = "${local.container_registry_login_server}/${var.container_image_repository}:${var.container_image_tag}"
  container_tags = {
    project     = var.project_name
    environment = var.environment
    managed_by  = "terraform"
  }
}
