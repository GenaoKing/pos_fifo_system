locals {
  prefix = lower("${var.project_slug}-${var.environment}")

  common_tags = merge(
    {
      project     = var.project_slug
      environment = var.environment
      managed_by  = "terraform"
      roadmap     = "F3"
    },
    var.extra_tags
  )
}
