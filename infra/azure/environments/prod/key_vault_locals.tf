locals {
  key_vault_location = coalesce(var.key_vault_location, var.location)
  key_vault_name     = coalesce(var.key_vault_name, "${replace(var.project_slug, "-", "")}${var.environment}kv")
}
