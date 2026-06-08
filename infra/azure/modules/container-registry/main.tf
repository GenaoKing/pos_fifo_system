resource "azurerm_container_registry" "main" {
  name                = var.name
  location            = var.location
  resource_group_name = var.resource_group_name
  sku                 = var.sku

  # Evitamos credenciales estaticas. Container Apps usara Managed Identity + AcrPull.
  admin_enabled = false

  tags = var.tags
}
