resource "azurerm_user_assigned_identity" "github_actions" {
  count = var.enable_github_actions_identity ? 1 : 0

  name                = local.github_actions_identity_name
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name

  tags = local.container_tags
}

resource "azurerm_federated_identity_credential" "github_actions_develop" {
  count = var.enable_github_actions_identity ? 1 : 0

  name      = "github-${var.github_deploy_branch}"
  parent_id = azurerm_user_assigned_identity.github_actions[0].id
  issuer    = "https://token.actions.githubusercontent.com"
  subject   = local.github_actions_subject
  audience  = ["api://AzureADTokenExchange"]

  lifecycle {
    precondition {
      condition     = var.github_repository_owner != null && var.github_repository_name != null
      error_message = "github_repository_owner and github_repository_name are required when enable_github_actions_identity=true."
    }
  }
}

resource "azurerm_role_assignment" "github_actions_acr_push" {
  count = var.enable_github_actions_identity ? 1 : 0

  scope                = module.container_registry.id
  role_definition_name = "AcrPush"
  principal_id         = azurerm_user_assigned_identity.github_actions[0].principal_id
}

resource "azurerm_role_assignment" "github_actions_container_apps_contributor" {
  count = var.enable_github_actions_identity ? 1 : 0

  scope                = azurerm_resource_group.main.id
  role_definition_name = "Container Apps Contributor"
  principal_id         = azurerm_user_assigned_identity.github_actions[0].principal_id
}
