terraform {
  # administrator_password_wo del Flexible Server requiere Terraform 1.11+.
  required_version = ">= 1.11"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
  }
}
