output "id" {
  description = "ID del servidor PostgreSQL Flexible Server."
  value       = azurerm_postgresql_flexible_server.main.id
}

output "name" {
  description = "Nombre del servidor PostgreSQL Flexible Server."
  value       = azurerm_postgresql_flexible_server.main.name
}

output "fqdn" {
  description = "FQDN publico del servidor PostgreSQL."
  value       = azurerm_postgresql_flexible_server.main.fqdn
}

output "administrator_login" {
  description = "Usuario administrador configurado."
  value       = azurerm_postgresql_flexible_server.main.administrator_login
}

output "database_names" {
  description = "Bases iniciales administradas por Terraform."
  value       = sort(keys(azurerm_postgresql_flexible_server_database.database))
}
