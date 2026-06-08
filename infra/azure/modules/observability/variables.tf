variable "name_prefix" {
  description = "Prefijo comun, ejemplo posfifo-dev."
  type        = string
}

variable "resource_group_name" {
  description = "Resource Group donde se crean los recursos."
  type        = string
}

variable "location" {
  description = "Region Azure."
  type        = string
}

variable "retention_days" {
  description = "Retencion de logs en dias."
  type        = number
}

variable "tags" {
  description = "Tags comunes."
  type        = map(string)
  default     = {}
}
