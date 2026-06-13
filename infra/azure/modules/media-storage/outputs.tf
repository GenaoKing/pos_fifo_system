output "id" {
  description = "ID del Storage Account de media."
  value       = azurerm_storage_account.main.id
}

output "name" {
  description = "Nombre del Storage Account de media."
  value       = azurerm_storage_account.main.name
}

output "container_name" {
  description = "Nombre del container publico de media."
  value       = azurerm_storage_container.media_public.name
}

output "primary_blob_endpoint" {
  description = "Endpoint base del servicio Blob."
  value       = azurerm_storage_account.main.primary_blob_endpoint
}
