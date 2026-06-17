variable "project_name" {
  description = "Prefijo corto del proyecto usado para nombres de recursos nuevos."
  type        = string
  default     = "posfifo"
}

variable "container_apps_location" {
  description = "Region para Container Apps. Null usa var.location."
  type        = string
  default     = null
}

variable "container_apps_environment_name" {
  description = "Nombre del Container Apps Environment. Null usa convencion local."
  type        = string
  default     = null
}

variable "existing_container_apps_environment_id" {
  description = "ID de un Container Apps Environment existente. Null crea uno nuevo."
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
  description = "Repositorio dentro del ACR compartido."
  type        = string
  default     = "pos-fifo-backend"
}

variable "container_image_tag" {
  description = "Tag de imagen bootstrap para Terraform. CI/CD despliega SHA; prod usa el tag estable prod."
  type        = string
  default     = "prod"

  validation {
    condition = !(
      (var.enable_api_container_app || var.enable_migrate_job) &&
      (trimspace(var.container_image_tag) == "" || var.container_image_tag == "latest" || var.container_image_tag == "prod-REEMPLAZAR_SHA")
    )
    error_message = "Para activar API/job en prod, container_image_tag debe existir en ACR y no puede ser latest ni prod-REEMPLAZAR_SHA."
  }
}

variable "django_secret_key" {
  description = "SECRET_KEY de Django cloud. Null si use_key_vault_secrets=true."
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
  description = "Replicas minimas de la API. Prod MVP usa 0 para scale-to-zero."
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
  default     = "prod"
}

variable "git_commit_sha" {
  description = "SHA expuesto por /api/v1/health/."
  type        = string
  default     = "unknown"
}

variable "db_name" {
  description = "Nombre de la DB control-plane prod. Null usa pos_fifo_prod."
  type        = string
  nullable    = true
  default     = null
}

variable "db_user" {
  description = "Usuario PostgreSQL. Null cuando use_key_vault_secrets=true y se cargue db-user en Key Vault."
  type        = string
  nullable    = true
  default     = null
}

variable "db_password" {
  description = "Password PostgreSQL. Null si use_key_vault_secrets=true."
  type        = string
  sensitive   = true
  nullable    = true
  default     = null
}

variable "db_host" {
  description = "Host PostgreSQL. Null usa fqdn de platform."
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
  description = "Timeout de conexion PostgreSQL en segundos."
  type        = string
  default     = "5"
}
