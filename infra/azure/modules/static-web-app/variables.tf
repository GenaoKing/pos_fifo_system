variable "name_prefix" {
  description = "Prefijo comun, ejemplo posfifo-dev."
  type        = string
}

variable "resource_group_name" {
  description = "Resource Group donde se crea Static Web App."
  type        = string
}

variable "location" {
  description = "Region soportada por Azure Static Web Apps."
  type        = string
}

variable "sku_tier" {
  description = "Tier de Static Web App: Free o Standard."
  type        = string
}

variable "sku_size" {
  description = "Size de Static Web App: Free o Standard."
  type        = string
}

variable "tags" {
  description = "Tags comunes."
  type        = map(string)
  default     = {}
}
