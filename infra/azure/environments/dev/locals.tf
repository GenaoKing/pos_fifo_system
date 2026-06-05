# Los locals son valores calculados. Mantienen nombres/tags consistentes sin repetir.
locals {
  prefix                 = lower("${var.project_slug}-${var.environment}")
  observability_location = coalesce(var.observability_location, var.location)

  common_tags = merge(
    {
      project     = var.project_slug
      environment = var.environment
      managed_by  = "terraform"
      roadmap     = "D2"
    },
    var.extra_tags
  )
}
