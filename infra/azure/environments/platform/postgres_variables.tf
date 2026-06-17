variable "postgres_server_name" {
  description = "Nombre globalmente unico del PostgreSQL Flexible Server. Null usa convencion local."
  type        = string
  default     = null
}

variable "postgres_admin_login" {
  description = "Usuario administrador del PostgreSQL platform."
  type        = string
  default     = "posadmin"
}

variable "postgres_admin_password" {
  description = "Password administrador inicial del PostgreSQL platform. Pasar por TF_VAR_postgres_admin_password, no en terraform.tfvars."
  type        = string
  sensitive   = true
}

variable "postgres_admin_password_version" {
  description = "Version manual del password write-only. Incrementar cuando se rote el password."
  type        = number
  default     = 1
}

variable "postgres_zone" {
  description = "Availability zone actual del PostgreSQL platform. Usar para evitar drift despues de creado."
  type        = string
  nullable    = true
  default     = null
}

variable "postgresql_version" {
  description = "Version mayor de PostgreSQL."
  type        = string
  default     = "16"
}

variable "postgres_sku_name" {
  description = "SKU de PostgreSQL Flexible Server."
  type        = string
  default     = "B_Standard_B1ms"
}

variable "postgres_storage_mb" {
  description = "Storage inicial en MB."
  type        = number
  default     = 32768
}

variable "postgres_backup_retention_days" {
  description = "Dias de retencion de backups automaticos."
  type        = number
  default     = 7
}

variable "postgres_database_names" {
  description = "Bases iniciales. Los tenants se crean luego por bootstrap_tenant."
  type        = list(string)
  default     = ["pos_fifo_prod"]
}

variable "postgres_firewall_rules" {
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
