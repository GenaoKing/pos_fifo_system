locals {
  django_secret_key_vault_uri          = var.key_vault_uri == null ? null : "${trimsuffix(var.key_vault_uri, "/")}/secrets/${var.django_secret_key_secret_name}"
  db_user_vault_uri                    = var.key_vault_uri == null ? null : "${trimsuffix(var.key_vault_uri, "/")}/secrets/${var.db_user_secret_name}"
  db_password_vault_uri                = var.key_vault_uri == null ? null : "${trimsuffix(var.key_vault_uri, "/")}/secrets/${var.db_password_secret_name}"
  web_push_vapid_private_key_vault_uri = var.key_vault_uri == null ? null : "${trimsuffix(var.key_vault_uri, "/")}/secrets/${var.web_push_vapid_private_key_secret_name}"

  container_app_environment_id   = var.existing_environment_id == null ? azurerm_container_app_environment.main[0].id : var.existing_environment_id
  container_app_environment_name = var.existing_environment_id == null ? azurerm_container_app_environment.main[0].name : coalesce(var.existing_environment_name, var.environment_name)
}

resource "azurerm_container_app_environment" "main" {
  count = var.existing_environment_id == null ? 1 : 0

  name                       = var.environment_name
  location                   = var.location
  resource_group_name        = var.resource_group_name
  log_analytics_workspace_id = var.log_analytics_workspace_id

  tags = var.tags
}

resource "azurerm_user_assigned_identity" "api" {
  count = var.enable_api ? 1 : 0

  name                = "${var.api_name}-id"
  location            = var.location
  resource_group_name = var.resource_group_name

  tags = var.tags
}

resource "azurerm_user_assigned_identity" "migrate" {
  count = var.enable_migrate_job ? 1 : 0

  name                = "${var.migrate_job_name}-id"
  location            = var.location
  resource_group_name = var.resource_group_name

  tags = var.tags
}

resource "azurerm_user_assigned_identity" "notifications" {
  count = var.enable_notifications_job ? 1 : 0

  name                = "${var.notifications_job_name}-id"
  location            = var.location
  resource_group_name = var.resource_group_name

  tags = var.tags
}

resource "azurerm_role_assignment" "api_acr_pull" {
  count = var.enable_api ? 1 : 0

  scope                = var.registry_id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.api[0].principal_id
}

resource "azurerm_role_assignment" "migrate_acr_pull" {
  count = var.enable_migrate_job ? 1 : 0

  scope                = var.registry_id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.migrate[0].principal_id
}

resource "azurerm_role_assignment" "notifications_acr_pull" {
  count = var.enable_notifications_job ? 1 : 0

  scope                = var.registry_id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.notifications[0].principal_id
}

resource "azurerm_role_assignment" "api_key_vault_secrets_user" {
  count = var.enable_api && var.use_key_vault_secrets ? 1 : 0

  scope                = var.key_vault_id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.api[0].principal_id
}

resource "azurerm_role_assignment" "migrate_key_vault_secrets_user" {
  count = var.enable_migrate_job && var.use_key_vault_secrets ? 1 : 0

  scope                = var.key_vault_id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.migrate[0].principal_id
}

resource "azurerm_role_assignment" "notifications_key_vault_secrets_user" {
  count = var.enable_notifications_job ? 1 : 0

  scope                = var.key_vault_id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.notifications[0].principal_id
}

resource "azurerm_role_assignment" "api_media_blob_contributor" {
  count = var.enable_api && var.enable_blob_media ? 1 : 0

  scope                = var.media_storage_account_id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_user_assigned_identity.api[0].principal_id
}

resource "azurerm_role_assignment" "migrate_media_blob_contributor" {
  count = var.enable_migrate_job && var.enable_blob_media ? 1 : 0

  scope                = var.media_storage_account_id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_user_assigned_identity.migrate[0].principal_id
}

resource "azurerm_container_app" "api" {
  count = var.enable_api ? 1 : 0

  name                         = var.api_name
  container_app_environment_id = local.container_app_environment_id
  resource_group_name          = var.resource_group_name
  revision_mode                = "Single"

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.api[0].id]
  }

  registry {
    server   = var.registry_server
    identity = azurerm_user_assigned_identity.api[0].id
  }

  secret {
    name                = "django-secret-key"
    value               = var.use_key_vault_secrets ? null : var.django_secret_key
    key_vault_secret_id = var.use_key_vault_secrets ? local.django_secret_key_vault_uri : null
    identity            = var.use_key_vault_secrets ? azurerm_user_assigned_identity.api[0].id : null
  }

  secret {
    name                = "db-password"
    value               = var.use_key_vault_secrets ? null : var.db_password
    key_vault_secret_id = var.use_key_vault_secrets ? local.db_password_vault_uri : null
    identity            = var.use_key_vault_secrets ? azurerm_user_assigned_identity.api[0].id : null
  }

  secret {
    name                = "db-user"
    value               = var.use_key_vault_secrets ? null : var.db_user
    key_vault_secret_id = var.use_key_vault_secrets ? local.db_user_vault_uri : null
    identity            = var.use_key_vault_secrets ? azurerm_user_assigned_identity.api[0].id : null
  }

  ingress {
    external_enabled           = true
    target_port                = 8000
    transport                  = "auto"
    allow_insecure_connections = false

    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    min_replicas = var.api_min_replicas
    max_replicas = var.api_max_replicas

    container {
      name   = "api"
      image  = var.image
      cpu    = var.api_cpu
      memory = var.api_memory

      env {
        name  = "DJANGO_SETTINGS_MODULE"
        value = "config.settings_cloud"
      }

      env {
        name        = "DJANGO_SECRET_KEY"
        secret_name = "django-secret-key"
      }

      env {
        name  = "ALLOWED_HOSTS"
        value = var.allowed_hosts
      }

      env {
        name  = "CORS_ALLOWED_ORIGINS"
        value = var.cors_allowed_origins
      }

      env {
        name  = "CSRF_TRUSTED_ORIGINS"
        value = var.csrf_trusted_origins
      }

      env {
        name  = "CLOUD_ENVIRONMENT"
        value = var.cloud_environment
      }

      env {
        name  = "APP_VERSION"
        value = var.app_version
      }

      env {
        name  = "GIT_COMMIT_SHA"
        value = var.git_commit_sha
      }

      env {
        name  = "DB_NAME"
        value = var.db_name
      }

      env {
        name        = "DB_USER"
        secret_name = "db-user"
      }

      env {
        name        = "DB_PASSWORD"
        secret_name = "db-password"
      }

      env {
        name  = "DB_HOST"
        value = var.db_host
      }

      env {
        name  = "DB_PORT"
        value = var.db_port
      }

      env {
        name  = "DB_SSLMODE"
        value = var.db_sslmode
      }

      env {
        name  = "PGCONNECT_TIMEOUT"
        value = var.db_connect_timeout
      }

      env {
        name  = "SECURE_SSL_REDIRECT"
        value = "true"
      }

      env {
        name  = "AZURE_BLOB_MEDIA_ENABLED"
        value = tostring(var.enable_blob_media)
      }

      env {
        name  = "AZURE_STORAGE_ACCOUNT_NAME"
        value = var.media_storage_account_name
      }

      env {
        name  = "AZURE_STORAGE_MEDIA_CONTAINER"
        value = var.media_storage_container_name
      }

      env {
        name  = "AZURE_CLIENT_ID"
        value = azurerm_user_assigned_identity.api[0].client_id
      }

      env {
        name  = "TENANCY_DB_PER_TENANT_ENABLED"
        value = tostring(var.enable_db_per_tenant)
      }

      env {
        name  = "WEB_PUSH_ENABLED"
        value = tostring(var.web_push_enabled)
      }

      env {
        name  = "WEB_PUSH_VAPID_PUBLIC_KEY"
        value = var.web_push_vapid_public_key
      }

      env {
        name  = "WEB_PUSH_VAPID_SUBJECT"
        value = var.web_push_vapid_subject
      }

      startup_probe {
        transport = "TCP"
        port      = 8000
      }

      liveness_probe {
        transport               = "TCP"
        port                    = 8000
        interval_seconds        = 30
        timeout                 = 5
        failure_count_threshold = 3
      }
    }
  }

  tags = var.tags

  lifecycle {
    ignore_changes = [
      template[0].container[0].image,
    ]
  }

  depends_on = [
    azurerm_role_assignment.api_acr_pull,
    azurerm_role_assignment.api_key_vault_secrets_user,
    azurerm_role_assignment.api_media_blob_contributor,
  ]
}

resource "azurerm_container_app_job" "migrate" {
  count = var.enable_migrate_job ? 1 : 0

  name                         = var.migrate_job_name
  location                     = var.location
  resource_group_name          = var.resource_group_name
  container_app_environment_id = local.container_app_environment_id

  replica_timeout_in_seconds = 1800
  replica_retry_limit        = 1

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.migrate[0].id]
  }

  registry {
    server   = var.registry_server
    identity = azurerm_user_assigned_identity.migrate[0].id
  }

  secret {
    name                = "django-secret-key"
    value               = var.use_key_vault_secrets ? null : var.django_secret_key
    key_vault_secret_id = var.use_key_vault_secrets ? local.django_secret_key_vault_uri : null
    identity            = var.use_key_vault_secrets ? azurerm_user_assigned_identity.migrate[0].id : null
  }

  secret {
    name                = "db-password"
    value               = var.use_key_vault_secrets ? null : var.db_password
    key_vault_secret_id = var.use_key_vault_secrets ? local.db_password_vault_uri : null
    identity            = var.use_key_vault_secrets ? azurerm_user_assigned_identity.migrate[0].id : null
  }

  secret {
    name                = "db-user"
    value               = var.use_key_vault_secrets ? null : var.db_user
    key_vault_secret_id = var.use_key_vault_secrets ? local.db_user_vault_uri : null
    identity            = var.use_key_vault_secrets ? azurerm_user_assigned_identity.migrate[0].id : null
  }

  manual_trigger_config {
    parallelism              = 1
    replica_completion_count = 1
  }

  template {
    container {
      name   = "migrate"
      image  = var.image
      cpu    = 0.5
      memory = "1Gi"

      command = var.migrate_command
      args    = var.migrate_args

      env {
        name  = "DJANGO_SETTINGS_MODULE"
        value = "config.settings_cloud"
      }

      env {
        name        = "DJANGO_SECRET_KEY"
        secret_name = "django-secret-key"
      }

      env {
        name  = "ALLOWED_HOSTS"
        value = var.allowed_hosts
      }

      env {
        name  = "CLOUD_ENVIRONMENT"
        value = var.cloud_environment
      }

      env {
        name  = "APP_VERSION"
        value = var.app_version
      }

      env {
        name  = "GIT_COMMIT_SHA"
        value = var.git_commit_sha
      }

      env {
        name  = "DB_NAME"
        value = var.db_name
      }

      env {
        name        = "DB_USER"
        secret_name = "db-user"
      }

      env {
        name        = "DB_PASSWORD"
        secret_name = "db-password"
      }

      env {
        name  = "DB_HOST"
        value = var.db_host
      }

      env {
        name  = "DB_PORT"
        value = var.db_port
      }

      env {
        name  = "DB_SSLMODE"
        value = var.db_sslmode
      }

      env {
        name  = "PGCONNECT_TIMEOUT"
        value = var.db_connect_timeout
      }

      env {
        name  = "AZURE_BLOB_MEDIA_ENABLED"
        value = tostring(var.enable_blob_media)
      }

      env {
        name  = "AZURE_STORAGE_ACCOUNT_NAME"
        value = var.media_storage_account_name
      }

      env {
        name  = "AZURE_STORAGE_MEDIA_CONTAINER"
        value = var.media_storage_container_name
      }

      env {
        name  = "AZURE_CLIENT_ID"
        value = azurerm_user_assigned_identity.migrate[0].client_id
      }

      env {
        name  = "TENANCY_DB_PER_TENANT_ENABLED"
        value = tostring(var.enable_db_per_tenant)
      }
    }
  }

  tags = var.tags

  lifecycle {
    ignore_changes = [
      template[0].container[0].image,
    ]
  }

  depends_on = [
    azurerm_role_assignment.migrate_acr_pull,
    azurerm_role_assignment.migrate_key_vault_secrets_user,
    azurerm_role_assignment.migrate_media_blob_contributor,
  ]
}

resource "azurerm_container_app_job" "notifications" {
  count = var.enable_notifications_job ? 1 : 0

  name                         = var.notifications_job_name
  location                     = var.location
  resource_group_name          = var.resource_group_name
  container_app_environment_id = local.container_app_environment_id

  replica_timeout_in_seconds = 300
  replica_retry_limit        = 1

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.notifications[0].id]
  }

  registry {
    server   = var.registry_server
    identity = azurerm_user_assigned_identity.notifications[0].id
  }

  secret {
    name                = "django-secret-key"
    value               = var.use_key_vault_secrets ? null : var.django_secret_key
    key_vault_secret_id = var.use_key_vault_secrets ? local.django_secret_key_vault_uri : null
    identity            = var.use_key_vault_secrets ? azurerm_user_assigned_identity.notifications[0].id : null
  }

  secret {
    name                = "db-password"
    value               = var.use_key_vault_secrets ? null : var.db_password
    key_vault_secret_id = var.use_key_vault_secrets ? local.db_password_vault_uri : null
    identity            = var.use_key_vault_secrets ? azurerm_user_assigned_identity.notifications[0].id : null
  }

  secret {
    name                = "db-user"
    value               = var.use_key_vault_secrets ? null : var.db_user
    key_vault_secret_id = var.use_key_vault_secrets ? local.db_user_vault_uri : null
    identity            = var.use_key_vault_secrets ? azurerm_user_assigned_identity.notifications[0].id : null
  }

  secret {
    name                = "web-push-vapid-private-key"
    key_vault_secret_id = local.web_push_vapid_private_key_vault_uri
    identity            = azurerm_user_assigned_identity.notifications[0].id
  }

  schedule_trigger_config {
    cron_expression          = var.notifications_schedule_cron
    parallelism              = 1
    replica_completion_count = 1
  }

  template {
    container {
      name    = "notifications"
      image   = var.image
      cpu     = 0.5
      memory  = "1Gi"
      command = ["python"]
      args    = ["manage.py", "procesar_notificaciones", "--settings=config.settings_cloud"]

      env {
        name  = "DJANGO_SETTINGS_MODULE"
        value = "config.settings_cloud"
      }

      env {
        name        = "DJANGO_SECRET_KEY"
        secret_name = "django-secret-key"
      }

      env {
        name  = "CLOUD_ENVIRONMENT"
        value = var.cloud_environment
      }

      # settings_cloud valida ALLOWED_HOSTS incluso en comandos de gestion.
      # Sin esta variable el job termina al importar Django, antes de procesar
      # el primer tenant.
      env {
        name  = "ALLOWED_HOSTS"
        value = var.api_allowed_hosts
      }

      env {
        name  = "DB_NAME"
        value = var.db_name
      }

      env {
        name        = "DB_USER"
        secret_name = "db-user"
      }

      env {
        name        = "DB_PASSWORD"
        secret_name = "db-password"
      }

      env {
        name  = "DB_HOST"
        value = var.db_host
      }

      env {
        name  = "DB_PORT"
        value = var.db_port
      }

      env {
        name  = "DB_SSLMODE"
        value = var.db_sslmode
      }

      env {
        name  = "PGCONNECT_TIMEOUT"
        value = var.db_connect_timeout
      }

      env {
        name  = "AZURE_CLIENT_ID"
        value = azurerm_user_assigned_identity.notifications[0].client_id
      }

      env {
        name  = "TENANCY_DB_PER_TENANT_ENABLED"
        value = tostring(var.enable_db_per_tenant)
      }

      env {
        name  = "WEB_PUSH_ENABLED"
        value = tostring(var.web_push_enabled)
      }

      env {
        name  = "WEB_PUSH_VAPID_PUBLIC_KEY"
        value = var.web_push_vapid_public_key
      }

      env {
        name        = "WEB_PUSH_VAPID_PRIVATE_KEY"
        secret_name = "web-push-vapid-private-key"
      }

      env {
        name  = "WEB_PUSH_VAPID_SUBJECT"
        value = var.web_push_vapid_subject
      }
    }
  }

  tags = var.tags

  lifecycle {
    ignore_changes = [template[0].container[0].image]

    precondition {
      condition = (
        var.use_key_vault_secrets &&
        var.key_vault_id != null &&
        var.key_vault_uri != null &&
        var.web_push_enabled &&
        trimspace(var.web_push_vapid_public_key) != ""
      )
      error_message = "El job de notificaciones exige Key Vault y un par VAPID publico configurado."
    }
  }

  depends_on = [
    azurerm_role_assignment.notifications_acr_pull,
    azurerm_role_assignment.notifications_key_vault_secrets_user,
  ]
}
