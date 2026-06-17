# Terraform crea el contenedor de hosting; el contenido React lo sube el pipeline.
resource "azurerm_static_web_app" "main" {
  name                = "${var.name_prefix}-portal-swa"
  resource_group_name = var.resource_group_name
  location            = var.location
  sku_tier            = var.sku_tier
  sku_size            = var.sku_size
  tags                = var.tags

  lifecycle {
    ignore_changes = [
      repository_branch,
      repository_url,
    ]
  }
}
