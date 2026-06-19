variable "project_name" {
  description = "Prefijo corto del proyecto usado para nombres de recursos nuevos."
  type        = string
  default     = "posfifo"
}

variable "acr_name" {
  description = "Nombre globalmente unico del ACR. Null usa una convencion basada en project/environment."
  type        = string
  default     = null
}

variable "acr_sku" {
  description = "SKU del Azure Container Registry."
  type        = string
  default     = "Basic"
}

variable "existing_container_registry_id" {
  description = "ID de un ACR existente a reutilizar. Si se define, staging no crea ACR propio."
  type        = string
  nullable    = true
  default     = null
}

variable "existing_container_registry_name" {
  description = "Nombre del ACR existente reutilizado para outputs."
  type        = string
  nullable    = true
  default     = null
}

variable "existing_container_registry_login_server" {
  description = "Login server del ACR existente, por ejemplo posfifodevacr.azurecr.io."
  type        = string
  nullable    = true
  default     = null
}

variable "container_apps_location" {
  description = "Region para ACR y Container Apps. Null usa var.location."
  type        = string
  default     = null
}

variable "container_apps_environment_name" {
  description = "Nombre del Container Apps Environment. Null usa convencion local."
  type        = string
  default     = null
}

variable "existing_container_apps_environment_id" {
  description = "ID de un Container Apps Environment existente. Usar en Azure for Students si la region solo permite un environment."
  type        = string
  nullable    = true
  default     = null
}

variable "existing_container_apps_environment_name" {
  description = "Nombre descriptivo del Container Apps Environment existente reutilizado para outputs."
  type        = string
  nullable    = true
  default     = null
}

variable "enable_api_container_app" {
  description = "Crea la API en Container Apps. Activar despues de publicar la imagen en ACR."
  type        = bool
  default     = false
}

variable "enable_migrate_job" {
  description = "Crea el job manual de migraciones. Activar despues de publicar la imagen en ACR."
  type        = bool
  default     = false
}

variable "enable_db_per_tenant" {
  description = "Activa TENANCY_DB_PER_TENANT_ENABLED en API y migrate job."
  type        = bool
  default     = true
}

variable "api_container_app_name" {
  description = "Nombre de la Container App API. Null usa convencion local."
  type        = string
  default     = null
}

variable "migrate_job_name" {
  description = "Nombre del Container App Job de migraciones. Null usa convencion local."
  type        = string
  default     = null
}

variable "container_image_repository" {
  description = "Repositorio dentro del ACR."
  type        = string
  default     = "pos-fifo-backend"
}

variable "container_image_tag" {
  description = "Tag de imagen a desplegar."
  type        = string
  default     = "staging"
}

variable "django_secret_key" {
  description = "SECRET_KEY de Django cloud. Requerido cuando enable_api_container_app o enable_migrate_job son true."
  type        = string
  sensitive   = true
  nullable    = true
  default     = null
}

variable "api_allowed_hosts" {
  description = "ALLOWED_HOSTS de Django para la API cloud."
  type        = string
  default     = ".azurecontainerapps.io,localhost,127.0.0.1"
}

variable "api_cors_allowed_origins" {
  description = "CORS_ALLOWED_ORIGINS de Django para la API cloud."
  type        = string
  default     = ""
}

variable "api_csrf_trusted_origins" {
  description = "CSRF_TRUSTED_ORIGINS de Django para la API cloud."
  type        = string
  default     = ""
}

variable "api_min_replicas" {
  description = "Replicas minimas de la API. En staging usar 0 para permitir scale-to-zero."
  type        = number
  default     = 0
}

variable "api_max_replicas" {
  description = "Replicas maximas de la API."
  type        = number
  default     = 1
}

variable "app_version" {
  description = "Version logica expuesta por /api/v1/health/."
  type        = string
  default     = "staging"
}

variable "git_commit_sha" {
  description = "SHA expuesto por /api/v1/health/."
  type        = string
  default     = "unknown"
}

variable "db_name" {
  description = "Nombre de la DB PostgreSQL staging existente. Puede vivir en el mismo server free que dev/prod, pero debe ser otra base."
  type        = string
  nullable    = true
  default     = null
}

variable "db_user" {
  description = "Usuario de PostgreSQL existente."
  type        = string
  nullable    = true
  default     = null
}

variable "db_password" {
  description = "Password de PostgreSQL existente. Queda en tfvars local y state; D3 lo movera a Key Vault/secrets."
  type        = string
  sensitive   = true
  nullable    = true
  default     = null
}

variable "db_host" {
  description = "Host de PostgreSQL existente."
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
  description = "SSL mode PostgreSQL."
  type        = string
  default     = "require"
}

variable "db_connect_timeout" {
  description = "Timeout de conexion PostgreSQL en segundos para evitar health checks colgados."
  type        = string
  default     = "5"
}
