output "media_storage" {
  description = "Datos operativos del Storage Account de media. Null si enable_media_storage=false."
  value = var.enable_media_storage ? {
    name                  = module.media_storage[0].name
    container_name        = module.media_storage[0].container_name
    primary_blob_endpoint = module.media_storage[0].primary_blob_endpoint
  } : null
}
