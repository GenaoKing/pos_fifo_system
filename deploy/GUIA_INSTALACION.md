# POS FIFO System - Guia de Instalacion v3

## Prerequisitos

Instalar en la PC del cliente antes de ejecutar el instalador:

1. **Python 3.12+** desde [python.org](https://www.python.org/downloads/)
   - Marcar **"Add Python to PATH"**
   - Marcar **"Install for all users"**

2. **PostgreSQL 15+** desde [postgresql.org](https://www.postgresql.org/download/windows/)
   - Anotar la contrasena del usuario `postgres` (se pide durante la instalacion)
   - Agregar al PATH del sistema: `C:\Program Files\PostgreSQL\15\bin`

3. **Drivers de impresoras** (opcional, se puede hacer despues)
   - Driver impresora termica 2Connect 2C-POS80-01
   - Driver Zebra ZDesigner LP 2824 (si aplica)

---

## Instalacion Rapida

### Paso 1: Copiar el proyecto
Copiar toda la carpeta `pos_fifo_system` a la PC del cliente.
Ruta recomendada: `C:\pos_fifo_system`

### Paso 2: Ejecutar el instalador
Click derecho en `deploy\instalar.bat` > **Ejecutar como administrador**

El instalador pedira:
- La contrasena del usuario `postgres` de PostgreSQL
- La primera vez, abrira `env_cliente.bat` para configurar la contrasena de la BD

El instalador hace todo automaticamente:
- Crea entorno virtual e instala dependencias (psycopg v3, waitress, whitenoise)
- Crea usuario y base de datos en PostgreSQL
- Ejecuta migraciones
- Recolecta archivos estaticos
- Crea usuario **Santiago** (SYSADMIN) con contrasena **Prueba123**
- Configura el negocio con el preset seleccionado (ConfiguracionNegocio)
- Crea la **Caja Principal**
- Genera SECRET_KEY unica

### Paso 3: Iniciar el sistema
Ejecutar `deploy\iniciar_servidor.bat`

### Paso 4: Primer acceso
Abrir en el navegador: `http://localhost:8080`

Login:
- **Usuario:** Santiago
- **Contrasena:** Prueba123
- **Cambiar la contrasena despues del primer login**

### Paso 5: Configurar el negocio
Con el usuario Santiago (SYSADMIN), ir a **Configuracion del Sistema** para:
- Ajustar nombre del negocio, RNC, direccion, telefono
- Activar/desactivar modulos segun el cliente
- Configurar metodos de pago
- Configurar nombres de impresoras

---

## Post-instalacion

### Crear usuarios del negocio
Desde el panel de administracion, crear:
- **ADMIN**: Dueno/administrador del negocio
- **CAJERA**: Operador(a) del punto de venta

### Inicio automatico con Windows
```
deploy\registrar_servicio.bat    (ejecutar como admin)
```
Si tiene NSSM (`nssm.exe` en deploy\), lo registra como servicio.
Si no, usa Task Scheduler como alternativa.

### Backups automaticos
```
deploy\programar_backup.bat      (ejecutar como admin)
```
Programa backup diario a las 11:00 PM. Mantiene los ultimos 30 en ZIP.

### Verificar instalacion
```
cd C:\pos_fifo_system
venv\Scripts\activate
call deploy\env_cliente.bat
python deploy\verificar_sistema.py
```

---

## Acceso desde otra PC (LAN)

1. En la PC servidor, abrir el Firewall de Windows
2. Crear regla de entrada para el puerto 8080 (TCP)
3. Desde la otra PC: `http://[IP-del-servidor]:8080`

Ver la IP del servidor: ejecutar `ipconfig` en la terminal.

---

## Comandos Utiles

| Accion | Comando |
|--------|---------|
| Iniciar servidor | `deploy\iniciar_servidor.bat` |
| Detener servidor | `deploy\detener_servidor.bat` |
| Backup manual | `deploy\backup_db.bat` |
| Verificar sistema | `python deploy\verificar_sistema.py` |
| Ver logs | `notepad logs\pos_system.log` |

---

## Solucion de Problemas

**El servidor no inicia:**
- Verificar que PostgreSQL este corriendo (buscar en Servicios de Windows)
- Revisar `logs\pos_system.log`
- Ejecutar `deploy\verificar_sistema.py`

**Error UnicodeDecodeError al conectar BD:**
- Verificar que se usa `psycopg[binary]` (v3), NO `psycopg2-binary`
- `pip uninstall psycopg2-binary && pip install "psycopg[binary]"`

**CREATE DATABASE falla pero la BD si se creo:**
- Normal en Windows espanol (warning de codepage). El instalador v3 ya valida con `pg_database` en vez del exit code.

**La impresora no funciona:**
- Verificar nombre exacto en Dispositivos e Impresoras
- Configurar desde panel SYSADMIN o `env_cliente.bat`
- Reiniciar el servidor

**Error de conexion desde otra PC:**
- Verificar firewall (puerto 8080)
- Confirmar misma red
- Usar la IP correcta del servidor

---

## Estructura de deploy/

```
deploy/
  env_cliente.bat.template  <-- Template de configuracion
  env_cliente.bat           <-- Config real del cliente (no subir a Git)
  instalar.bat              <-- Instalador principal v3
  iniciar_servidor.bat      <-- Arrancar el sistema
  detener_servidor.bat      <-- Detener el sistema
  registrar_servicio.bat    <-- Auto-inicio con Windows
  programar_backup.bat      <-- Configurar backups diarios
  backup_db.bat             <-- Backup manual
  verificar_sistema.py      <-- Diagnostico del sistema v3
  preparar_paquete.bat      <-- Empaquetar para USB (solo en dev)
  nssm.exe                  <-- (Opcional) Gestor de servicios
```

---

## Cambios vs v2

- Datos del negocio ya NO van en `env_cliente.bat` → panel SYSADMIN via ConfiguracionNegocio
- Usuario Santiago (SYSADMIN) se crea automaticamente
- Caja Principal se crea automaticamente
- `crear_config_inicial` se ejecuta con preset del negocio
- Validacion de BD usa `pg_database` (no exit code de `CREATE DATABASE`)
- Encoding UTF8 forzado en todos los scripts
- Nombre de servicio NSSM cambiado a `POSFifoSystem` (generico multi-cliente)
- verificar_sistema.py ahora verifica alpine.min.js y chart.min.js locales
