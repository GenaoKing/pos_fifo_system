variable "name_prefix" {
  description = "Prefijo comun del ambiente, por ejemplo posfifo-dev."
  type        = string
}

variable "resource_group_name" {
  description = "Resource Group donde se crean el Action Group y la alerta."
  type        = string
}

variable "location" {
  description = "Region Azure para la regla de alerta."
  type        = string
}

variable "log_analytics_workspace_id" {
  description = "Workspace que recibe ContainerAppSystemLogs_CL del runtime del job."
  type        = string
}

variable "notifications_job_name" {
  description = "Nombre exacto del Container Apps Job vigilado."
  type        = string
}

variable "notifications_job_enabled" {
  description = "Confirma que el job existe; una alerta sin job seria ruido permanente."
  type        = bool
}

variable "alert_email" {
  description = "Correo del receptor del Action Group."
  type        = string
}

variable "tags" {
  description = "Tags comunes."
  type        = map(string)
  default     = {}
}
