variable "subscription_id" {
  description = "Azure Subscription ID donde se creara platform."
  type        = string
}

variable "project_slug" {
  description = "Prefijo corto para nombres de recursos."
  type        = string
  default     = "posfifo"
}

variable "environment" {
  description = "Nombre del ambiente. Este root module debe quedarse en platform."
  type        = string
  default     = "platform"

  validation {
    condition     = var.environment == "platform"
    error_message = "Este root module es solo para platform."
  }
}

variable "location" {
  description = "Region principal para recursos platform."
  type        = string
  default     = "canadacentral"
}

variable "extra_tags" {
  description = "Tags adicionales para auditoria/costos."
  type        = map(string)
  default     = {}
}
