# Royal Plastic POS - Guia de Instalacion

## Prerequisitos

Instalar en la PC del cliente antes de comenzar:

1. **Python 3.11+** desde [python.org](https://www.python.org/downloads/)
   - Durante la instalacion, marcar **"Add Python to PATH"**
   - Marcar **"Install for all users"**

2. **PostgreSQL 15+** desde [postgresql.org](https://www.postgresql.org/download/windows/)
   - Anotar la contrasena del usuario `postgres` durante la instalacion
   - Agregar al PATH del sistema: `C:\Program Files\PostgreSQL\15\bin`

3. **Drivers de impresoras**
   - Driver impresora termica 2Connect 2C-POS80-01
   - Driver Zebra ZDesigner LP 2824 (si aplica)

---

## Instalacion Rapida

### Paso 1: Copiar el proyecto
Copiar toda la carpeta `pos_fifo_system` a la PC del cliente.
Ruta recomendada: `C:\RoyalPlastic\pos_fifo_system`

### Paso 2: Configurar datos del cliente
Abrir `deploy\env_cliente.bat.template`, guardarlo como `deploy\env_cliente.bat` y editar:
- Contrasena de base de datos
- IP del servidor en la red local
- Nombres exactos de las impresoras (como aparecen en Windows)
- Datos del negocio (RNC, direccion, telefono)

### Paso 3: Ejecutar el instalador
Click derecho en `deploy\instalar.bat` > **Ejecutar como administrador**

El instalador hace todo automaticamente:
- Crea entorno virtual de Python
- Instala todas las dependencias
- Crea la base de datos PostgreSQL
- Ejecuta las migraciones
- Recolecta archivos estaticos
- Crea el usuario administrador

### Paso 4: Verificar la instalacion
```
cd C:\RoyalPlastic\pos_fifo_system
venv\Scripts\activate
python deploy\verificar_sistema.py
```

### Paso 5: Iniciar el sistema
Ejecutar `deploy\iniciar_servidor.bat`

Acceder desde el navegador: `http://localhost:8080`

---

## Inicio Automatico

Para que el sistema arranque al encender la PC:

**Opcion A (recomendada): NSSM**
1. Descargar NSSM de https://nssm.cc/download
2. Colocar `nssm.exe` en la carpeta `deploy\`
3. Ejecutar como administrador: `deploy\registrar_servicio.bat`

**Opcion B: Task Scheduler**
El script `registrar_servicio.bat` lo configura automaticamente si no encuentra NSSM.

---

## Backups Automaticos

Ejecutar como administrador: `deploy\programar_backup.bat`

Esto programa un backup diario a las 11:00 PM. Los backups se guardan en la carpeta `backups\` comprimidos en ZIP, conservando los ultimos 30.

Para un backup manual: `deploy\backup_db.bat`

---

## Acceso desde otra PC (LAN)

Si la cajera usa otra computadora en la misma red:

1. En la PC servidor, abrir el Firewall de Windows
2. Crear regla de entrada para el puerto 8080 (TCP)
3. Desde la otra PC, acceder a: `http://[IP-del-servidor]:8080`

La IP del servidor se puede ver ejecutando `ipconfig` en la terminal.

---

## Comandos Utiles

| Accion | Comando |
|--------|---------|
| Iniciar servidor | `deploy\iniciar_servidor.bat` |
| Detener servidor | `deploy\detener_servidor.bat` |
| Backup manual | `deploy\backup_db.bat` |
| Verificar sistema | `python deploy\verificar_sistema.py` |
| Ver logs | Abrir `logs\pos_system.log` |

---

## Solucion de Problemas

**El servidor no inicia:**
- Verificar que PostgreSQL este corriendo (buscar en Servicios de Windows)
- Revisar `logs\pos_system.log` para errores
- Ejecutar `deploy\verificar_sistema.py` para diagnostico

**La impresora no funciona:**
- Verificar nombre exacto en Dispositivos e Impresoras
- Actualizar el nombre en `deploy\env_cliente.bat`
- Reiniciar el servidor

**Error de conexion desde otra PC:**
- Verificar que el firewall permita el puerto 8080
- Confirmar que ambas PCs estan en la misma red
- Usar la IP correcta del servidor

---

## Estructura de la carpeta deploy

```
deploy/
  env_cliente.bat.template  <-- Template de configuracion
  env_cliente.bat           <-- Configuracion real (no subir a Git)
  instalar.bat              <-- Instalador principal
  iniciar_servidor.bat      <-- Arrancar el sistema
  detener_servidor.bat      <-- Detener el sistema
  registrar_servicio.bat    <-- Auto-inicio con Windows
  programar_backup.bat      <-- Configurar backups diarios
  backup_db.bat             <-- Backup manual
  verificar_sistema.py      <-- Diagnostico del sistema
  nssm.exe                  <-- (Opcional) Gestor de servicios
```
