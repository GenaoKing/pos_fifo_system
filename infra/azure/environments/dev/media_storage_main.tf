module "media_storage" {
  count = var.enable_media_storage ? 1 : 0

  source = "../../modules/media-storage"

  name                                = local.media_storage_account_name
  location                            = var.location
  resource_group_name                 = azurerm_resource_group.main.name
  container_name                      = var.media_storage_container_name
  grant_current_user_blob_contributor = var.grant_current_user_media_blob_contributor
  tags                                = local.common_tags
}
