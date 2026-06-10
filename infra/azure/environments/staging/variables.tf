# Copiar terraform.tfvars.example -> terraform.tfvars y llenar subscription_id.
# terraform.tfvars NO se commitea.

variable "subscription_id" {
  description = "Azure Subscription ID donde se creara el ambiente staging."
  type        = string
}

variable "project_slug" {
  description = "Prefijo corto para nombres de recursos. Usar minusculas, numeros y guiones."
  type        = string
  default     = "posfifo"
}

variable "environment" {
  description = "Nombre del ambiente. Este root module debe quedarse en staging."
  type        = string
  default     = "staging"

  validation {
    condition     = var.environment == "staging"
    error_message = "Este root module es solo para staging. Usar dev/prod en sus carpetas."
  }
}

variable "location" {
  description = "Region principal para recursos Azure."
  type        = string
  default     = "canadacentral"
}

variable "observability_location" {
  description = "Region para Log Analytics/Application Insights. Null usa var.location."
  type        = string
  default     = null
}

variable "static_web_app_location" {
  description = "Region soportada por Azure Static Web Apps."
  type        = string
  default     = "canadacentral"
}

variable "static_web_app_sku_tier" {
  description = "Tier de Static Web App. Free para staging inicial si la suscripcion lo permite."
  type        = string
  default     = "Free"
}

variable "static_web_app_sku_size" {
  description = "Size de Static Web App. Normalmente coincide con sku_tier."
  type        = string
  default     = "Free"
}

variable "log_retention_days" {
  description = "Dias de retencion de Log Analytics. 30 mantiene costo bajo para staging."
  type        = number
  default     = 30
}

variable "extra_tags" {
  description = "Tags adicionales para auditoria/costos."
  type        = map(string)
  default     = {}
}
variable "enable_static_web_app" {
  description = "Crea Azure Static Web Apps. En algunas suscripciones puede estar bloqueado por policy regional."
  type        = bool
  default     = false
}
