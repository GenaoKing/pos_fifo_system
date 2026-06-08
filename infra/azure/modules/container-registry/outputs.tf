output "id" {
  description = "ID del Azure Container Registry."
  value       = azurerm_container_registry.main.id
}

output "name" {
  description = "Nombre del Azure Container Registry."
  value       = azurerm_container_registry.main.name
}

output "login_server" {
  description = "Servidor Docker del registry, por ejemplo nombre.azurecr.io."
  value       = azurerm_container_registry.main.login_server
}
