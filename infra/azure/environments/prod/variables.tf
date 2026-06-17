variable "subscription_id" {
  description = "Azure Subscription ID donde se creara prod."
  type        = string
}

variable "project_slug" {
  description = "Prefijo corto para nombres de recursos."
  type        = string
  default     = "posfifo"
}

variable "environment" {
  description = "Nombre del ambiente. Este root module debe quedarse en prod."
  type        = string
  default     = "prod"

  validation {
    condition     = var.environment == "prod"
    error_message = "Este root module es solo para prod."
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
  default     = "centralus"
}

variable "static_web_app_sku_tier" {
  description = "Tier de Static Web App. Free para prod MVP."
  type        = string
  default     = "Free"
}

variable "static_web_app_sku_size" {
  description = "Size de Static Web App. Normalmente coincide con sku_tier."
  type        = string
  default     = "Free"
}

variable "log_retention_days" {
  description = "Dias de retencion de Log Analytics."
  type        = number
  default     = 30
}

variable "extra_tags" {
  description = "Tags adicionales para auditoria/costos."
  type        = map(string)
  default     = {}
}

variable "enable_static_web_app" {
  description = "Crea Azure Static Web Apps para el portal prod."
  type        = bool
  default     = false
}
