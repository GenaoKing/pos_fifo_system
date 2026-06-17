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

module "static_web_app" {
  count = var.enable_static_web_app ? 1 : 0

  source = "../../modules/static-web-app"

  name_prefix         = local.prefix
  resource_group_name = azurerm_resource_group.main.name
  location            = var.static_web_app_location
  sku_tier            = var.static_web_app_sku_tier
  sku_size            = var.static_web_app_sku_size
  tags                = local.common_tags
}
