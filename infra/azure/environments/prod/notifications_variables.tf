variable "enable_notifications_job" {
  description = "Crea el job programado de notificaciones; habilitar solo despues del piloto."
  type        = bool
  default     = false
}

variable "notifications_job_name" {
  description = "Nombre del job. Null usa la convencion del ambiente."
  type        = string
  nullable    = true
  default     = null
}

variable "web_push_enabled" {
  type    = bool
  default = false
}

variable "web_push_vapid_public_key" {
  type    = string
  default = ""
}

variable "web_push_vapid_private_key_secret_name" {
  type    = string
  default = "web-push-vapid-private-key"
}

variable "web_push_vapid_subject" {
  type    = string
  default = "mailto:admin@example.com"
}
