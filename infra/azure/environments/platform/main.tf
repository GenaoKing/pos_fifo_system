resource "azurerm_resource_group" "main" {
  name     = "${local.prefix}-rg"
  location = var.location
  tags     = local.common_tags
}

data "azurerm_container_registry" "shared" {
  name                = var.shared_acr_name
  resource_group_name = var.shared_acr_resource_group_name
}

module "postgresql" {
  source = "../../modules/postgresql-flexible-server"

  name                           = local.postgres_server_name
  location                       = var.location
  zone                           = var.postgres_zone
  resource_group_name            = azurerm_resource_group.main.name
  postgresql_version             = var.postgresql_version
  administrator_login            = var.postgres_admin_login
  administrator_password         = var.postgres_admin_password
  administrator_password_version = var.postgres_admin_password_version
  sku_name                       = var.postgres_sku_name
  storage_mb                     = var.postgres_storage_mb
  backup_retention_days          = var.postgres_backup_retention_days
  database_names                 = var.postgres_database_names
  firewall_rules                 = var.postgres_firewall_rules
  geo_redundant_backup_enabled   = false
  auto_grow_enabled              = true

  tags = local.common_tags
}
