# Terraform no habla con Azure por si solo: este provider es el "driver".
# En azurerm v4 el subscription_id es requerido para plan/apply.
provider "azurerm" {
  features {
    # En staging inicial el Resource Group es efimero/apagable.
    # Si un apply parcial deja recursos anidados, permitimos que Azure borre el
    # grupo completo durante destroy. No copiar esta decision a prod sin revisar.
    resource_group {
      prevent_deletion_if_contains_resources = false
    }
  }

  subscription_id = var.subscription_id

  # Storage con shared_access_key_enabled=false: Terraform autentica el data-plane
  # de Storage (poll de blob service, contenedores) via Azure AD, no account keys.
  # Requiere que el principal que corre `terraform apply` tenga un rol
  # "Storage Blob Data Contributor/Owner" en el scope del RG (o superior).
  # Ver docs/runbooks/AZURE_BLOB_MEDIA.md.
  storage_use_azuread = true
}
