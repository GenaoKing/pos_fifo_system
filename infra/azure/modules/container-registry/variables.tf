variable "name" {
  description = "Nombre globalmente unico del Azure Container Registry."
  type        = string
}

variable "location" {
  description = "Region Azure donde se crea el registry."
  type        = string
}

variable "resource_group_name" {
  description = "Resource Group donde se crea el registry."
  type        = string
}

variable "sku" {
  description = "SKU del registry. Basic es suficiente para dev."
  type        = string
  default     = "Basic"
}

variable "tags" {
  description = "Tags comunes."
  type        = map(string)
  default     = {}
}
