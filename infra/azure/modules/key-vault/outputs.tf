output "id" {
  description = "ID del Key Vault."
  value       = azurerm_key_vault.main.id
}

output "name" {
  description = "Nombre del Key Vault."
  value       = azurerm_key_vault.main.name
}

output "vault_uri" {
  description = "URI publica del Key Vault."
  value       = azurerm_key_vault.main.vault_uri
}
