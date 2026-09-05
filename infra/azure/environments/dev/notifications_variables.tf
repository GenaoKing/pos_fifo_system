variable "enable_notifications_job" {
  description = "Crea el job programado de notificaciones; mantener false hasta completar la verificacion."
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
  description = "Habilita registro y entrega Web Push."
  type        = bool
  default     = false
}

variable "web_push_vapid_public_key" {
  description = "Clave publica VAPID de este ambiente."
  type        = string
  default     = ""
}

variable "web_push_vapid_private_key_secret_name" {
  description = "Secreto Key Vault con la clave privada VAPID."
  type        = string
  default     = "web-push-vapid-private-key"
}

variable "web_push_vapid_subject" {
  description = "Contacto VAPID."
  type        = string
  default     = "mailto:admin@example.com"
}
