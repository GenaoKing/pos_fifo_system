variable "enable_key_vault" {
  description = "Crea Key Vault para secretos cloud."
  type        = bool
  default     = true
}

variable "key_vault_name" {
  description = "Nombre globalmente unico del Key Vault. Null usa convencion local."
  type        = string
  default     = null
}

variable "key_vault_location" {
  description = "Region del Key Vault. Null usa var.location."
  type        = string
  default     = null
}

variable "key_vault_purge_protection_enabled" {
  description = "Purge protection de Key Vault. False en dev para poder destruir labs; true en prod."
  type        = bool
  default     = false
}

variable "grant_current_user_key_vault_secrets_officer" {
  description = "Permite que el usuario/principal actual cargue secrets en Key Vault."
  type        = bool
  default     = true
}

variable "use_key_vault_secrets" {
  description = "Hace que Container Apps lea django-secret-key y db-password desde Key Vault."
  type        = bool
  default     = false
}

variable "django_secret_key_secret_name" {
  description = "Nombre del secreto de Key Vault para DJANGO_SECRET_KEY."
  type        = string
  default     = "django-secret-key"
}

variable "db_password_secret_name" {
  description = "Nombre del secreto de Key Vault para DB_PASSWORD."
  type        = string
  default     = "db-password"
}
