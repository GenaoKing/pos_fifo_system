variable "environment_name" {
  description = "Nombre del Container Apps Environment."
  type        = string
}

variable "existing_environment_id" {
  description = "ID de un Container Apps Environment existente. Si se define, el modulo no crea uno nuevo."
  type        = string
  nullable    = true
  default     = null
}

variable "existing_environment_name" {
  description = "Nombre descriptivo del Container Apps Environment reutilizado. Solo se usa para outputs cuando existing_environment_id esta definido."
  type        = string
  nullable    = true
  default     = null
}

variable "location" {
  description = "Region Azure para Container Apps."
  type        = string
}

variable "resource_group_name" {
  description = "Resource Group donde viven los recursos."
  type        = string
}

variable "log_analytics_workspace_id" {
  description = "Workspace usado por Container Apps para logs."
  type        = string
}

variable "registry_id" {
  description = "ID del Azure Container Registry."
  type        = string
}

variable "registry_server" {
  description = "Servidor del ACR, por ejemplo nombre.azurecr.io."
  type        = string
}

variable "image" {
  description = "Imagen completa a desplegar en Container Apps."
  type        = string
}

variable "enable_api" {
  description = "Crea la Container App de la API. Activar despues de publicar una imagen existente en ACR."
  type        = bool
  default     = false
}

variable "enable_migrate_job" {
  description = "Crea el job manual de migraciones. Activar despues de publicar una imagen existente en ACR."
  type        = bool
  default     = false
}

variable "enable_notifications_job" {
  description = "Crea el job programado que proyecta y entrega notificaciones."
  type        = bool
  default     = false
}

variable "api_name" {
  description = "Nombre de la Container App API."
  type        = string
}

variable "migrate_job_name" {
  description = "Nombre del Container App Job de migraciones."
  type        = string
}

variable "notifications_job_name" {
  description = "Nombre del Container App Job de notificaciones."
  type        = string
}

variable "notifications_schedule_cron" {
  description = "Cron UTC de cinco campos para el job de notificaciones."
  type        = string
  default     = "*/1 * * * *"
}

variable "django_secret_key" {
  description = "SECRET_KEY para Django cloud."
  type        = string
  sensitive   = true
  nullable    = true
  default     = null
}

variable "allowed_hosts" {
  description = "ALLOWED_HOSTS para Django, separados por coma."
  type        = string
  default     = ".azurecontainerapps.io,localhost,127.0.0.1"
}

variable "cors_allowed_origins" {
  description = "CORS_ALLOWED_ORIGINS para Django, separados por coma."
  type        = string
  default     = ""
}

variable "csrf_trusted_origins" {
  description = "CSRF_TRUSTED_ORIGINS para Django, separados por coma."
  type        = string
  default     = ""
}

variable "cloud_environment" {
  description = "Nombre logico del ambiente Django."
  type        = string
  default     = "dev"
}

variable "app_version" {
  description = "Version logica expuesta en health."
  type        = string
  default     = "dev"
}

variable "git_commit_sha" {
  description = "SHA de commit expuesto en health."
  type        = string
  default     = "unknown"
}

variable "db_name" {
  description = "Nombre de la base PostgreSQL."
  type        = string
  nullable    = true
  default     = null
}

variable "db_user" {
  description = "Usuario PostgreSQL."
  type        = string
  nullable    = true
  default     = null
}

variable "db_password" {
  description = "Password PostgreSQL."
  type        = string
  sensitive   = true
  nullable    = true
  default     = null
}

variable "db_host" {
  description = "Host PostgreSQL."
  type        = string
  nullable    = true
  default     = null
}

variable "db_port" {
  description = "Puerto PostgreSQL."
  type        = string
  default     = "5432"
}

variable "db_sslmode" {
  description = "SSL mode para PostgreSQL."
  type        = string
  default     = "require"
}

variable "db_connect_timeout" {
  description = "Timeout de conexion PostgreSQL en segundos para evitar health checks colgados."
  type        = string
  default     = "5"
}

variable "use_key_vault_secrets" {
  description = "Usa referencias a Key Vault para django-secret-key, db-user y db-password en vez de valores directos en Terraform."
  type        = bool
  default     = false
}

variable "key_vault_id" {
  description = "ID del Key Vault usado para asignar permisos a las Managed Identities."
  type        = string
  nullable    = true
  default     = null
}

variable "key_vault_uri" {
  description = "URI del Key Vault, por ejemplo https://vault.vault.azure.net/."
  type        = string
  nullable    = true
  default     = null
}

variable "django_secret_key_secret_name" {
  description = "Nombre del secreto en Key Vault para DJANGO_SECRET_KEY."
  type        = string
  default     = "django-secret-key"
}

variable "db_password_secret_name" {
  description = "Nombre del secreto en Key Vault para DB_PASSWORD."
  type        = string
  default     = "db-password"
}

variable "db_user_secret_name" {
  description = "Nombre del secreto en Key Vault para DB_USER."
  type        = string
  default     = "db-user"
}

variable "web_push_enabled" {
  description = "Expone la clave publica y habilita el envio Web Push."
  type        = bool
  default     = false
}

variable "web_push_vapid_public_key" {
  description = "Clave publica VAPID, distinta por ambiente."
  type        = string
  default     = ""
}

variable "web_push_vapid_private_key_secret_name" {
  description = "Nombre del secreto Key Vault con la clave privada VAPID."
  type        = string
  default     = "web-push-vapid-private-key"
}

variable "web_push_vapid_subject" {
  description = "Contacto VAPID, por ejemplo mailto:operaciones@example.com."
  type        = string
  default     = "mailto:admin@example.com"
}

variable "enable_blob_media" {
  description = "Activa media publica en Azure Blob Storage para settings_cloud."
  type        = bool
  default     = false
}

variable "enable_db_per_tenant" {
  description = "Activa el modo DB-per-tenant (TENANCY_DB_PER_TENANT_ENABLED) en API y migrate job."
  type        = bool
  default     = false
}

variable "migrate_command" {
  description = "Comando base del Container App Job de migraciones."
  type        = list(string)
  default     = ["python"]
}

variable "migrate_args" {
  description = "Argumentos del Container App Job de migraciones."
  type        = list(string)
  default     = ["manage.py", "migrate_cloud", "--settings=config.settings_cloud", "--noinput"]
}

variable "media_storage_account_id" {
  description = "ID del Storage Account de media para asignar permisos Blob."
  type        = string
  nullable    = true
  default     = null
}

variable "media_storage_account_name" {
  description = "Nombre del Storage Account de media."
  type        = string
  default     = ""
}

variable "media_storage_container_name" {
  description = "Nombre del blob container de media."
  type        = string
  default     = "media-public"
}

variable "api_cpu" {
  description = "CPU de la API en plan Consumption."
  type        = number
  default     = 0.5
}

variable "api_memory" {
  description = "Memoria de la API en plan Consumption."
  type        = string
  default     = "1Gi"
}

variable "api_min_replicas" {
  description = "Replicas minimas de la API."
  type        = number
  default     = 0
}

variable "api_max_replicas" {
  description = "Replicas maximas de la API."
  type        = number
  default     = 1
}

variable "tags" {
  description = "Tags comunes."
  type        = map(string)
  default     = {}
}
