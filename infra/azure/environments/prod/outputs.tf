output "resource_group" {
  description = "Resource Group principal del ambiente prod."
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

output "postgres" {
  description = "Datos no secretos de PostgreSQL consumidos desde platform."
  value = {
    host    = local.prod_db_host
    db_name = local.prod_db_name
    db_user = var.use_key_vault_secrets ? "key-vault:${var.db_user_secret_name}" : local.prod_db_user
    port    = var.db_port
    sslmode = var.db_sslmode
  }
}
