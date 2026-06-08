module "key_vault" {
  count = var.enable_key_vault ? 1 : 0

  source = "../../modules/key-vault"

  name                               = local.key_vault_name
  location                           = local.key_vault_location
  resource_group_name                = azurerm_resource_group.main.name
  purge_protection_enabled           = var.key_vault_purge_protection_enabled
  grant_current_user_secrets_officer = var.grant_current_user_key_vault_secrets_officer
  tags                               = local.container_tags
}
