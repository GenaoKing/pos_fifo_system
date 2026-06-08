output "github_actions_identity" {
  description = "Datos para configurar GitHub Actions OIDC. Null si enable_github_actions_identity=false."
  value = var.enable_github_actions_identity ? {
    name         = azurerm_user_assigned_identity.github_actions[0].name
    client_id    = azurerm_user_assigned_identity.github_actions[0].client_id
    principal_id = azurerm_user_assigned_identity.github_actions[0].principal_id
    subject      = local.github_actions_subject
  } : null
}
