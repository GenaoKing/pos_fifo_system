variable "name" {
  description = "Nombre globalmente unico del Storage Account para media publica."
  type        = string
}

variable "location" {
  description = "Region Azure para el Storage Account."
  type        = string
}

variable "resource_group_name" {
  description = "Resource Group donde vive el Storage Account."
  type        = string
}

variable "container_name" {
  description = "Nombre del blob container publico para media no sensible."
  type        = string
  default     = "media-public"
}

variable "grant_current_user_blob_contributor" {
  description = "Permite al principal actual subir blobs manualmente con Azure CLI para migracion/smoke tests."
  type        = bool
  default     = true
}

variable "tags" {
  description = "Tags comunes."
  type        = map(string)
  default     = {}
}
