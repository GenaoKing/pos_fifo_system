variable "name" {
  description = "Nombre globalmente unico del Key Vault."
  type        = string
}

variable "location" {
  description = "Region Azure del Key Vault."
  type        = string
}

variable "resource_group_name" {
  description = "Resource Group donde se crea el Key Vault."
  type        = string
}

variable "sku_name" {
  description = "SKU de Key Vault."
  type        = string
  default     = "standard"
}

variable "soft_delete_retention_days" {
  description = "Dias de retencion para soft delete."
  type        = number
  default     = 7
}

variable "purge_protection_enabled" {
  description = "Habilita purge protection. Recomendado en prod; en dev queda false para poder destruir labs."
  type        = bool
  default     = false
}

variable "grant_current_user_secrets_officer" {
  description = "Asigna Key Vault Secrets Officer al principal que ejecuta Terraform."
  type        = bool
  default     = true
}

variable "tags" {
  description = "Tags comunes."
  type        = map(string)
  default     = {}
}
