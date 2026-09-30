# Provider simulado: estas pruebas no crean recursos ni consultan Azure.
mock_provider "azurerm" {}

variables {
  environment_name           = "notifications-test-env"
  existing_environment_id    = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/test-rg/providers/Microsoft.App/managedEnvironments/test-env"
  location                   = "eastus2"
  resource_group_name        = "test-rg"
  log_analytics_workspace_id = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/test-rg/providers/Microsoft.OperationalInsights/workspaces/test-law"
  registry_id                = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/test-rg/providers/Microsoft.ContainerRegistry/registries/testregistry"
  registry_server            = "testregistry.azurecr.io"
  image                      = "testregistry.azurecr.io/backend@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  api_name                   = "test-api"
  migrate_job_name           = "test-migrate"
  notifications_job_name     = "test-notifications"
  enable_notifications_job   = true
  notifications_trigger_type = "Manual"
  web_push_enabled           = false
  use_key_vault_secrets      = true
  key_vault_id               = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/test-rg/providers/Microsoft.KeyVault/vaults/test-vault"
  key_vault_uri              = "https://test-vault.vault.azure.net/"
  web_push_vapid_public_key  = "public-key-test-only"
}

run "manual_allows_disabled_push" {
  command = plan

  assert {
    condition = (
      length(azurerm_container_app_job.notifications[0].manual_trigger_config) == 1 &&
      length(azurerm_container_app_job.notifications[0].schedule_trigger_config) == 0 &&
      azurerm_container_app_job.notifications[0].template[0].container[0].cpu == 0.5 &&
      azurerm_container_app_job.notifications[0].template[0].container[0].memory == "1Gi" &&
      one([for env in azurerm_container_app_job.notifications[0].template[0].container[0].env : env.value if env.name == "WEB_PUSH_ENABLED"]) == "false"
    )
    error_message = "Preparar el job debe conservar Manual, 0.5 CPU/1 GiB y Web Push apagado."
  }
}

run "scheduled_requires_enabled_push" {
  command = plan
  variables {
    notifications_trigger_type = "Schedule"
  }
  expect_failures = [azurerm_container_app_job.notifications]
}

run "manual_still_requires_public_key" {
  command = plan
  variables {
    web_push_vapid_public_key = ""
  }
  expect_failures = [azurerm_container_app_job.notifications]
}

run "manual_still_requires_private_secret_reference" {
  command = plan
  variables {
    web_push_vapid_private_key_secret_name = ""
  }
  expect_failures = [azurerm_container_app_job.notifications]
}
