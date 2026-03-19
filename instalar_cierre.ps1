# Como administrador
nssm install "CierreCajaProgramado" "C:\pos_fifo_system\scripts\ejecutar_servicio_cierre.bat"

# Configurar
nssm set "CierreCajaProgramado" DisplayName "Cierre de Caja Programado - POS FIFO"
nssm set "CierreCajaProgramado" Description "Genera cierre diario a las 7:00 PM"
nssm set "CierreCajaProgramado" Start SERVICE_AUTO_START

# Configurar logs
nssm set "CierreCajaProgramado" AppStdout "C:\pos_fifo_system\logs\servicio_stdout.log"
nssm set "CierreCajaProgramado" AppStderr "C:\pos_fifo_system\logs\servicio_stderr.log"

# Iniciar
nssm start "CierreCajaProgramado"
