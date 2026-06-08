variable "enable_github_actions_identity" {
  description = "Crea una User Assigned Managed Identity para GitHub Actions OIDC."
  type        = bool
  default     = false
}

variable "github_actions_identity_name" {
  description = "Nombre de la Managed Identity usada por GitHub Actions. Null usa convencion local."
  type        = string
  default     = null
}

variable "github_repository_owner" {
  description = "Owner u organizacion del repo GitHub, por ejemplo santiago o mi-org."
  type        = string
  default     = null

  validation {
    condition     = var.github_repository_owner == null || can(regex("^[A-Za-z0-9_.-]+$", var.github_repository_owner))
    error_message = "github_repository_owner debe ser solo el owner/org de GitHub, por ejemplo GenaoKing. No uses URLs."
  }
}

variable "github_repository_name" {
  description = "Nombre del repo GitHub."
  type        = string
  default     = null

  validation {
    condition     = var.github_repository_name == null || can(regex("^[A-Za-z0-9_.-]+$", var.github_repository_name))
    error_message = "github_repository_name debe ser solo el nombre del repo, por ejemplo pos_fifo_system. No uses https://github.com/owner/repo."
  }
}

variable "github_deploy_branch" {
  description = "Branch autorizado para hacer deploy dev."
  type        = string
  default     = "develop"

  validation {
    condition     = can(regex("^[^[:space:]]+$", var.github_deploy_branch))
    error_message = "github_deploy_branch no debe contener espacios. Usa nombres como develop o features/cloud-dashboard."
  }
}
