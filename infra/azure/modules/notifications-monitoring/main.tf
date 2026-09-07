locals {
  # Azure limita short_name a 12 caracteres.
  action_group_short_name = "${substr(replace(var.name_prefix, "-", ""), 0, 9)}ntf"
}

resource "azurerm_monitor_action_group" "notifications" {
  name                = "${var.name_prefix}-notifications-ag"
  resource_group_name = var.resource_group_name
  short_name          = local.action_group_short_name
  enabled             = true
  tags                = var.tags

  email_receiver {
    name                    = "notifications-operations"
    email_address           = var.alert_email
    use_common_alert_schema = true
  }

  lifecycle {
    precondition {
      condition     = var.notifications_job_enabled
      error_message = "enable_notifications_alerts requiere enable_notifications_job=true."
    }
    precondition {
      condition     = trimspace(var.alert_email) != ""
      error_message = "notifications_alert_email es obligatorio cuando las alertas estan habilitadas."
    }
  }
}

resource "azurerm_monitor_scheduled_query_rules_alert_v2" "notifications" {
  name                = "${var.name_prefix}-notifications-job-missed"
  resource_group_name = var.resource_group_name
  location            = var.location

  display_name            = "${var.name_prefix}: job de notificaciones sin exito"
  description             = "Alerta si no hubo una ejecucion Completed exitosa del job en los ultimos cinco minutos."
  enabled                 = true
  severity                = 2
  evaluation_frequency    = "PT1M"
  window_duration         = "PT5M"
  scopes                  = [var.log_analytics_workspace_id]
  auto_mitigation_enabled = true
  tags                    = var.tags

  criteria {
    query = <<-KQL
      ContainerAppSystemLogs_CL
      | where TimeGenerated > ago(5m)
      | where JobName_s == '${var.notifications_job_name}'
      | where Reason_s == 'Completed'
      | where Log_s has 'Execution' and Log_s has 'successfully completed'
      | summarize successful_executions = count()
      | where successful_executions == 0
    KQL

    time_aggregation_method = "Count"
    operator                = "GreaterThan"
    threshold               = 0

    failing_periods {
      number_of_evaluation_periods             = 1
      minimum_failing_periods_to_trigger_alert = 1
    }
  }

  action {
    action_groups = [azurerm_monitor_action_group.notifications.id]
    email_subject = "${var.name_prefix}: revisar job de notificaciones"
  }
}
