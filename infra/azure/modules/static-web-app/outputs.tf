output "id" {
  description = "ID del recurso Static Web App."
  value       = azurerm_static_web_app.main.id
}

output "name" {
  description = "Nombre del recurso Static Web App."
  value       = azurerm_static_web_app.main.name
}

output "default_host_name" {
  description = "Hostname default asignado por Azure."
  value       = azurerm_static_web_app.main.default_host_name
}
