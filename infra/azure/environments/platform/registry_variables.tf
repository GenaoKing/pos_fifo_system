variable "shared_acr_name" {
  description = "ACR compartido temporalmente para dev/staging/prod."
  type        = string
  default     = "posfifodevacr"
}

variable "shared_acr_resource_group_name" {
  description = "Resource Group donde vive el ACR compartido temporal."
  type        = string
  default     = "posfifo-dev-rg"
}
