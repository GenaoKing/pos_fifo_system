variable "enable_media_storage" {
  description = "Crea Storage Account economico para media publica cloud."
  type        = bool
  default     = false
}

variable "media_storage_account_name" {
  description = "Nombre globalmente unico del Storage Account de media. Null usa convencion local."
  type        = string
  default     = null
}

variable "media_storage_container_name" {
  description = "Container publico para imagenes no sensibles."
  type        = string
  default     = "media-public"
}

variable "grant_current_user_media_blob_contributor" {
  description = "Permite al principal actual subir blobs manualmente para migracion/smoke tests."
  type        = bool
  default     = true
}
