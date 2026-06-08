# Este root module es el punto desde donde se corre Terraform para el ambiente dev.
# Mantiene state local al inicio para aprendizaje: no hay bloque backend remoto aqui.
terraform {
  required_version = ">= 1.9"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
  }
}
