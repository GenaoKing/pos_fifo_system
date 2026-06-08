output "log_analytics_workspace_id" {
  description = "ID del workspace para otros modulos."
  value       = azurerm_log_analytics_workspace.main.id
}

output "log_analytics_workspace_name" {
  description = "Nombre del workspace."
  value       = azurerm_log_analytics_workspace.main.name
}

output "application_insights_id" {
  description = "ID de Application Insights."
  value       = azurerm_application_insights.main.id
}

output "application_insights_name" {
  description = "Nombre de Application Insights."
  value       = azurerm_application_insights.main.name
}

output "application_insights_connection_string" {
  description = "Connection string para instrumentar apps futuras. No es password, pero se marca sensible para no imprimirlo accidentalmente."
  value       = azurerm_application_insights.main.connection_string
  sensitive   = true
}
