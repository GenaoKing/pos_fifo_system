resource "azurerm_resource_group" "main" {
  name     = "${local.prefix}-rg"
  location = var.location
  tags     = local.common_tags
}

module "observability" {
  source = "../../modules/observability"

  name_prefix         = local.prefix
  resource_group_name = azurerm_resource_group.main.name
  location            = local.observability_location
  retention_days      = var.log_retention_days
  tags                = local.common_tags
}
