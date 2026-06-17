module "container_apps" {
  source = "../../modules/container-apps"

  environment_name           = local.container_apps_environment_name
  existing_environment_id    = var.existing_container_apps_environment_id
  existing_environment_name  = var.existing_container_apps_environment_name
  location                   = local.container_apps_location
  resource_group_name        = azurerm_resource_group.main.name
  log_analytics_workspace_id = module.observability.container_apps_log_analytics_workspace_id

  registry_id     = local.registry_id
  registry_server = local.registry_login_server
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
  db_name              = local.prod_db_name
  db_user              = local.prod_db_user
  db_password          = var.db_password
  db_host              = local.prod_db_host
  db_port              = var.db_port
  db_sslmode           = var.db_sslmode
  db_connect_timeout   = var.db_connect_timeout

  use_key_vault_secrets         = var.use_key_vault_secrets
  key_vault_id                  = var.enable_key_vault ? module.key_vault[0].id : null
  key_vault_uri                 = var.enable_key_vault ? module.key_vault[0].vault_uri : null
  django_secret_key_secret_name = var.django_secret_key_secret_name
  db_user_secret_name           = var.db_user_secret_name
  db_password_secret_name       = var.db_password_secret_name

  enable_blob_media            = var.enable_media_storage
  enable_db_per_tenant         = var.enable_db_per_tenant
  media_storage_account_id     = var.enable_media_storage ? module.media_storage[0].id : null
  media_storage_account_name   = var.enable_media_storage ? module.media_storage[0].name : ""
  media_storage_container_name = var.media_storage_container_name

  tags = local.container_tags
}
