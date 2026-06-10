locals {
  github_actions_identity_name = coalesce(var.github_actions_identity_name, "${var.project_name}-${var.environment}-github-actions-id")
  github_actions_subject       = var.github_repository_owner == null || var.github_repository_name == null ? null : "repo:${var.github_repository_owner}/${var.github_repository_name}:ref:refs/heads/${var.github_deploy_branch}"
}
