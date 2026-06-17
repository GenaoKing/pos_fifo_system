terraform {
  backend "azurerm" {
    resource_group_name  = "posfifo-tfstate-rg"
    storage_account_name = "posfifotfstatedev"
    container_name       = "tfstate"
    key                  = "azure/platform.tfstate"
    use_azuread_auth     = true
  }
}
