output "container_registry" {
  description = "Datos operativos del Azure Container Registry."
  value = {
    name         = module.container_registry.name
    login_server = module.container_registry.login_server
  }
}

output "container_apps" {
  description = "Datos operativos de Azure Container Apps."
  value = {
    environment_name = module.container_apps.environment_name
    api_fqdn         = module.container_apps.api_fqdn
    migrate_job_id   = module.container_apps.migrate_job_id
    image            = local.container_image
  }
}
