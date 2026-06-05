resource "azurerm_container_app_environment" "main" {
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

resource "azurerm_container_app" "api" {
  count = var.enable_api ? 1 : 0

  name                         = var.api_name
  container_app_environment_id = azurerm_container_app_environment.main.id
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
    name  = "django-secret-key"
    value = var.django_secret_key
  }

  secret {
    name  = "db-password"
    value = var.db_password
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
        name  = "DB_USER"
        value = var.db_user
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

      startup_probe {
        transport = "HTTP"
        port      = 8000
        path      = "/api/v1/health/live/"
        host      = "localhost"
      }

      liveness_probe {
        transport               = "HTTP"
        port                    = 8000
        path                    = "/api/v1/health/live/"
        host                    = "localhost"
        interval_seconds        = 30
        timeout                 = 5
        failure_count_threshold = 3
      }
    }
  }

  tags = var.tags

  depends_on = [azurerm_role_assignment.api_acr_pull]
}

resource "azurerm_container_app_job" "migrate" {
  count = var.enable_migrate_job ? 1 : 0

  name                         = var.migrate_job_name
  location                     = var.location
  resource_group_name          = var.resource_group_name
  container_app_environment_id = azurerm_container_app_environment.main.id

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
    name  = "django-secret-key"
    value = var.django_secret_key
  }

  secret {
    name  = "db-password"
    value = var.db_password
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

      command = ["python"]
      args    = ["manage.py", "migrate", "--settings=config.settings_cloud", "--noinput"]

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
        name  = "DB_USER"
        value = var.db_user
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
    }
  }

  tags = var.tags

  depends_on = [azurerm_role_assignment.migrate_acr_pull]
}
