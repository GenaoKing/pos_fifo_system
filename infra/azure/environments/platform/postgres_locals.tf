locals {
  postgres_server_name = coalesce(var.postgres_server_name, "${replace(var.project_slug, "-", "")}${var.environment}pg")
}
