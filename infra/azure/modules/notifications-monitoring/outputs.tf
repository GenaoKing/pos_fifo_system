output "action_group_id" {
  description = "ID del Action Group operativo."
  value       = azurerm_monitor_action_group.notifications.id
}

output "alert_rule_id" {
  description = "ID de la alerta por ausencia de ejecuciones exitosas."
  value       = azurerm_monitor_scheduled_query_rules_alert_v2.notifications.id
}
