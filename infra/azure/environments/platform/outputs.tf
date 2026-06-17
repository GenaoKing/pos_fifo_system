output "resource_group" {
  description = "Resource Group principal de platform."
  value = {
    name     = azurerm_resource_group.main.name
    location = azurerm_resource_group.main.location
    id       = azurerm_resource_group.main.id
  }
}

output "postgres" {
  description = "Datos de conexion no secretos del PostgreSQL platform."
  value = {
    server_id      = module.postgresql.id
    server_name    = module.postgresql.name
    fqdn           = module.postgresql.fqdn
    database_names = module.postgresql.database_names
    port           = "5432"
    sslmode        = "require"
  }
}

output "container_registry" {
  description = "ACR compartido temporal para imagenes backend."
  value = {
    id           = data.azurerm_container_registry.shared.id
    name         = data.azurerm_container_registry.shared.name
    login_server = data.azurerm_container_registry.shared.login_server
  }
}
