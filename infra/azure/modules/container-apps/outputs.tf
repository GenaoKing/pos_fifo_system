output "environment_id" {
  description = "ID del Container Apps Environment."
  value       = local.container_app_environment_id
}

output "environment_name" {
  description = "Nombre del Container Apps Environment."
  value       = local.container_app_environment_name
}

output "api_id" {
  description = "ID de la Container App API. Null si enable_api=false."
  value       = var.enable_api ? azurerm_container_app.api[0].id : null
}

output "api_fqdn" {
  description = "FQDN publico de la API. Null si enable_api=false."
  value       = var.enable_api ? azurerm_container_app.api[0].ingress[0].fqdn : null
}

output "migrate_job_id" {
  description = "ID del job de migraciones. Null si enable_migrate_job=false."
  value       = var.enable_migrate_job ? azurerm_container_app_job.migrate[0].id : null
}

output "notifications_job_id" {
  description = "ID del job programado de notificaciones. Null si esta deshabilitado."
  value       = var.enable_notifications_job ? azurerm_container_app_job.notifications[0].id : null
}

output "notifications_identity_principal_id" {
  description = "Principal ID de la identidad dedicada del job."
  value       = var.enable_notifications_job ? azurerm_user_assigned_identity.notifications[0].principal_id : null
}
