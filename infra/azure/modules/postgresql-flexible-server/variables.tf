variable "name" {
  description = "Nombre del Azure Database for PostgreSQL Flexible Server."
  type        = string
}

variable "location" {
  description = "Region Azure del servidor."
  type        = string
}

variable "zone" {
  description = "Availability zone del servidor. Null deja que Azure/provider decida."
  type        = string
  nullable    = true
  default     = null
}

variable "resource_group_name" {
  description = "Resource Group donde vive PostgreSQL."
  type        = string
}

variable "postgresql_version" {
  description = "Version mayor de PostgreSQL."
  type        = string
  default     = "16"
}

variable "administrator_login" {
  description = "Usuario administrador del servidor PostgreSQL."
  type        = string
  default     = "posadmin"
}

variable "administrator_password" {
  description = "Password administrador inicial del servidor PostgreSQL. Usar TF_VAR_ o prompt; se pasa al provider como write-only."
  type        = string
  sensitive   = true
}

variable "administrator_password_version" {
  description = "Version manual del password write-only. Incrementar cuando se rote el password."
  type        = number
  default     = 1
}

variable "sku_name" {
  description = "SKU de Flexible Server. B_Standard_B1ms mantiene bajo costo en MVP."
  type        = string
  default     = "B_Standard_B1ms"
}

variable "storage_mb" {
  description = "Storage inicial en MB."
  type        = number
  default     = 32768
}

variable "backup_retention_days" {
  description = "Dias de retencion de backups automaticos."
  type        = number
  default     = 7
}

variable "geo_redundant_backup_enabled" {
  description = "Activa backup geo-redundante. Mantener false en Azure for Students MVP."
  type        = bool
  default     = false
}

variable "auto_grow_enabled" {
  description = "Permite crecimiento automatico de storage."
  type        = bool
  default     = true
}

variable "public_network_access_enabled" {
  description = "Permite acceso publico controlado por firewall. Requerido sin VNET privada."
  type        = bool
  default     = true
}

variable "database_names" {
  description = "Bases iniciales creadas en el servidor. Los tenants se crean luego por la app."
  type        = list(string)
  default     = ["pos_fifo_prod"]
}

variable "database_charset" {
  description = "Charset de las bases iniciales."
  type        = string
  default     = "UTF8"
}

variable "database_collation" {
  description = "Collation de las bases iniciales."
  type        = string
  default     = "en_US.utf8"
}

variable "firewall_rules" {
  description = "Reglas de firewall por nombre."
  type = map(object({
    start_ip_address = string
    end_ip_address   = string
  }))
  default = {
    allow-azure-services = {
      start_ip_address = "0.0.0.0"
      end_ip_address   = "0.0.0.0"
    }
  }
}

variable "tags" {
  description = "Tags comunes."
  type        = map(string)
  default     = {}
}
