data "azurerm_container_app_environment" "notifications_runtime" {
  count = var.enable_notifications_alerts && var.existing_container_apps_environment_id != null ? 1 : 0

  name                = var.existing_container_apps_environment_name
  resource_group_name = split("/", var.existing_container_apps_environment_id)[4]
}

data "azurerm_log_analytics_workspace" "notifications_runtime" {
  count = var.enable_notifications_alerts && var.existing_container_apps_environment_id != null ? 1 : 0

  name                = data.azurerm_container_app_environment.notifications_runtime[0].log_analytics_workspace_name
  resource_group_name = split("/", var.existing_container_apps_environment_id)[4]
}

locals {
  notifications_monitor_workspace_id = var.existing_container_apps_environment_id == null ? module.observability.log_analytics_workspace_id : data.azurerm_log_analytics_workspace.notifications_runtime[0].id
}

module "notifications_monitoring" {
  count = var.enable_notifications_alerts ? 1 : 0

  source = "../../modules/notifications-monitoring"

  name_prefix                = local.prefix
  resource_group_name        = azurerm_resource_group.main.name
  location                   = local.observability_location
  log_analytics_workspace_id = local.notifications_monitor_workspace_id
  notifications_job_name     = local.notifications_job_name
  notifications_job_enabled  = var.enable_notifications_job
  alert_email                = var.notifications_alert_email
  tags                       = local.common_tags

  depends_on = [module.container_apps]
}
