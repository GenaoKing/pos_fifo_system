output "container_apps_log_analytics_workspace_id" {
  description = "Workspace ID usado por Azure Container Apps."
  value       = azurerm_log_analytics_workspace.main.id
}
