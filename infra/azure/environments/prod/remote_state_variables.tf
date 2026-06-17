variable "tfstate_resource_group_name" {
  description = "Resource Group del Storage Account de Terraform remote state."
  type        = string
  default     = "posfifo-tfstate-rg"
}

variable "tfstate_storage_account_name" {
  description = "Storage Account del Terraform remote state."
  type        = string
  default     = "posfifotfstatedev"
}

variable "tfstate_container_name" {
  description = "Container de blobs del Terraform remote state."
  type        = string
  default     = "tfstate"
}

variable "platform_state_key" {
  description = "Blob key del state platform."
  type        = string
  default     = "azure/platform.tfstate"
}
