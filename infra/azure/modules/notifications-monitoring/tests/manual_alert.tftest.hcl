mock_provider "azurerm" {}

variables {
  name_prefix                 = "test-prod"
  resource_group_name         = "test-rg"
  location                    = "eastus2"
  log_analytics_workspace_id  = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/test-rg/providers/Microsoft.OperationalInsights/workspaces/test-law"
  notifications_job_name      = "test-notifications"
  notifications_job_enabled   = true
  notifications_job_scheduled = false
  alert_email                 = "operations@example.com"
}

run "manual_disables_missed_run_alert" {
  command = plan
  assert {
    condition     = azurerm_monitor_scheduled_query_rules_alert_v2.notifications.enabled == false
    error_message = "Un job Manual no debe disparar alertas por ausencia de ejecuciones."
  }
}
