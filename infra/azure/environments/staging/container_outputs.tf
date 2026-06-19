output "container_registry" {
  description = "Datos operativos del Azure Container Registry."
  value = {
    name                  = local.container_registry_name
    login_server          = local.container_registry_login_server
    managed_by_this_state = !local.use_existing_container_registry
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
