# Log Analytics es el repositorio de logs/metricas. Container Apps lo usara luego.
resource "azurerm_log_analytics_workspace" "main" {
  name                = "${var.name_prefix}-law"
  resource_group_name = var.resource_group_name
  location            = var.location
  sku                 = "PerGB2018"
  retention_in_days   = var.retention_days
  tags                = var.tags
}

# Application Insights se monta sobre Log Analytics para telemetry de la app.
resource "azurerm_application_insights" "main" {
  name                = "${var.name_prefix}-appi"
  resource_group_name = var.resource_group_name
  location            = var.location
  application_type    = "web"
  workspace_id        = azurerm_log_analytics_workspace.main.id
  tags                = var.tags
}
