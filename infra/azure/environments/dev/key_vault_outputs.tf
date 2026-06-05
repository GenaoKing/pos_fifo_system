output "key_vault" {
  description = "Datos operativos del Key Vault. Null si enable_key_vault=false."
  value = var.enable_key_vault ? {
    name      = module.key_vault[0].name
    vault_uri = module.key_vault[0].vault_uri
  } : null
}
