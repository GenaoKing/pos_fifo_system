output "resource_group" {
  description = "Resource Group principal del ambiente staging."
  value = {
    name     = azurerm_resource_group.main.name
    location = azurerm_resource_group.main.location
    id       = azurerm_resource_group.main.id
  }
}

output "observability" {
  description = "Outputs del modulo de observabilidad."
  value       = module.observability
  sensitive   = true
}

output "static_web_app" {
  description = "Outputs de Azure Static Web Apps. Null si enable_static_web_app=false."
  value       = var.enable_static_web_app ? module.static_web_app[0] : null
  sensitive   = true
}

output "notifications_job_id" {
  value = module.container_apps.notifications_job_id
}
