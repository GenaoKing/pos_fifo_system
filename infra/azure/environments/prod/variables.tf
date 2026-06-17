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
