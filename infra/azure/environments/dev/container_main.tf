module "container_registry" {
  source = "../../modules/container-registry"

  name                = local.acr_name
  location            = local.container_apps_location
  resource_group_name = azurerm_resource_group.main.name
  sku                 = var.acr_sku
  tags                = local.container_tags
}

module "container_apps" {
  source = "../../modules/container-apps"

  environment_name           = local.container_apps_environment_name
  location                   = local.container_apps_location
  resource_group_name        = azurerm_resource_group.main.name
  log_analytics_workspace_id = module.observability.container_apps_log_analytics_workspace_id

  registry_id     = module.container_registry.id
  registry_server = module.container_registry.login_server
  image           = local.container_image

  enable_api         = var.enable_api_container_app
  enable_migrate_job = var.enable_migrate_job
  api_name           = local.api_container_app_name
  migrate_job_name   = local.migrate_job_name

  django_secret_key    = var.django_secret_key
  allowed_hosts        = var.api_allowed_hosts
  cors_allowed_origins = var.api_cors_allowed_origins
  csrf_trusted_origins = var.api_csrf_trusted_origins
  api_min_replicas     = var.api_min_replicas
  api_max_replicas     = var.api_max_replicas
  cloud_environment    = var.environment
  app_version          = var.app_version
  git_commit_sha       = var.git_commit_sha
  db_name              = var.db_name
  db_user              = var.db_user
  db_password          = var.db_password
  db_host              = var.db_host
  db_port              = var.db_port
  db_sslmode           = var.db_sslmode

  use_key_vault_secrets         = var.use_key_vault_secrets
  key_vault_id                  = var.enable_key_vault ? module.key_vault[0].id : null
  key_vault_uri                 = var.enable_key_vault ? module.key_vault[0].vault_uri : null
  django_secret_key_secret_name = var.django_secret_key_secret_name
  db_password_secret_name       = var.db_password_secret_name

  tags = local.container_tags
}
