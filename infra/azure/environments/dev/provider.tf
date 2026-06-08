# Terraform no habla con Azure por si solo: este provider es el "driver".
# En azurerm v4 el subscription_id es requerido para plan/apply.
provider "azurerm" {
  features {
    # En este ambiente dev/lab el Resource Group es efimero.
    # Si un apply parcial deja recursos anidados, permitimos que Azure borre el
    # grupo completo durante destroy. No copiar esta decision a prod sin revisar.
    resource_group {
      prevent_deletion_if_contains_resources = false
    }
  }

  subscription_id = var.subscription_id
}
