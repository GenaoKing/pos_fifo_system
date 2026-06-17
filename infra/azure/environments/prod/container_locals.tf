locals {
  platform_postgres = data.terraform_remote_state.platform.outputs.postgres
  platform_registry = data.terraform_remote_state.platform.outputs.container_registry

  container_apps_location         = coalesce(var.container_apps_location, var.location)
  container_apps_environment_name = coalesce(var.container_apps_environment_name, "${var.project_name}-${var.environment}-aca-env")
  api_container_app_name          = coalesce(var.api_container_app_name, "${var.project_name}-${var.environment}-api")
  migrate_job_name                = coalesce(var.migrate_job_name, "${var.project_name}-${var.environment}-migrate")

  registry_id           = local.platform_registry.id
  registry_login_server = local.platform_registry.login_server
  container_image       = "${local.registry_login_server}/${var.container_image_repository}:${var.container_image_tag}"

  prod_db_name = coalesce(var.db_name, "pos_fifo_prod")
  prod_db_user = var.db_user
  prod_db_host = coalesce(var.db_host, local.platform_postgres.fqdn)

  container_tags = {
    project     = var.project_name
    environment = var.environment
    managed_by  = "terraform"
  }
}
