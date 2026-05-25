# POS FIFO System — Roadmap Integral
## De sistema local a plataforma multi-sucursal con cloud

**Fecha:** Abril 2026  
**Estado actual:** Sistema POS local funcionando en producción (Royal Plast EIRL)

---

## Estado actual del proyecto

### Completado (✅)

**Core del sistema:**
- Modelos completos: Producto, Categoría, Lote, MovimientoLote, Venta, DetalleVenta, Pago, Compra, DetalleCompra, AjusteInventario
- Lógica FIFO completa en `fifo_logic.py`: consumo automático por fecha, valuación, stock disponible
- POS operativo: carrito dinámico, escaneo código de barras, descuentos por línea, pagos múltiples (efectivo/transferencia/mixto/tarjeta)
- Sistema de impresión: térmica 80mm (2Connect) + etiquetas Zebra LP 2824 (EPL2) + PrintManager singleton
- Cotizaciones: crear, listar, convertir a venta, PDF
- Clientes: CRUD + cliente contado + búsqueda en POS
- Anulaciones: con devolución a lotes originales + auditoría + límite configurable de días

**Infraestructura:**
- ConfiguracionNegocio: singleton con feature flags, cache invalidation, context processor, decoradores `@requiere_modulo` / `@requiere_sysadmin`
- Sistema de roles: SYSADMIN > ADMIN > CAJERA con permisos granulares
- Auditoría: modelo completo, middleware auto-logging, registro de ventas/anulaciones/login
- Deploy v3: instalar.bat, scripts de servicio (NSSM), backup automático, verificar_sistema.py
- Presets de cliente: plasticos, accesorios_auto, retail_general via `crear_config_inicial`
- Logging: RotatingFileHandler con separación all/errors
- Settings production con WhiteNoise (CompressedStaticFilesStorage)

**Reportes:**
- Dashboard con métricas en tiempo real (Alpine.js polling)
- Dashboard cajera (vista filtrada)
- Reportes On-Demand backend completo: cierre de caja, ventas por período, top productos, inventario valorizado FIFO, ventas por cajero
- Reportes On-Demand frontend completo: formularios dinámicos, Chart.js, export PDF
- PDF Generator con ReportLab

### Pendiente del sistema local (🔲)

- **Frontend anulaciones**: UI para que el cajero ejecute anulaciones desde el POS (backend listo)
- **UI ajustes de inventario**: interfaz para ajustes manuales (merma, daño) — backend listo
- **Dashboard auditoría frontend**: vistas stubbed en `auditoria/views.py`, templates pendientes
- **Migración ConfiguracionNegocio Fase 3**: mover `settings.BUSINESS_INFO` y `settings.THERMAL_PRINTER` hardcodeados a `get_config()` — se hace incrementalmente
- **Métodos de pago dinámicos en POS**: que el POS lea `get_metodos_pago()` en vez de tener los métodos hardcodeados en el template

---

## Roadmap por fases

### FASE 0 — Completar sistema local (prioridad inmediata)
> *Estabilizar lo que hay antes de agregar complejidad*

**0.1 Frontend anulaciones**
- UI en el POS para buscar venta y ejecutar anulación
- Confirmación con motivo obligatorio
- El backend ya maneja la lógica FIFO reversa

**0.2 Ajustes de inventario UI**
- Formulario para seleccionar producto → lote específico → tipo ajuste (merma/daño)
- Integración con MovimientoLote existente

**0.3 Métodos de pago dinámicos**
- POS lee `get_config().get_metodos_pago_activos()` en vez de hardcodear
- Template condicional: si `pago_tarjeta` está off, no muestra la opción

**0.4 Dashboard auditoría**
- Template para `auditoria/dashboard.html`
- Filtros por fecha, usuario, tipo de acción, nivel de importancia

---

### FASE 1 — Branches de base de datos cloud (exploración)
> *Aprender infraestructura cloud sin afectar producción*

**Branch `feature/azure-postgres`**
- `config/settings_neon.py` — conexión a Neon PostgreSQL (free tier permanente, 0.5 GB)
- `config/settings_azure.py` — conexión a Azure Database for PostgreSQL (free 12 meses)
- `deploy/env_neon.bat` + `deploy/env_azure.bat` + scripts de inicio
- Agregar `sslmode=require`, `CONN_MAX_AGE=600`, `CONN_HEALTH_CHECKS=True`
- Migrar, cargar config inicial, probar latencia desde RD
- Documentar resultados de latencia (ms por query, impacto en UX del POS)

**Branch `feature/azure-sql`**
- `config/settings_azure_sql.py` — conexión a Azure SQL Database (free permanente, 32 GB)
- Instalar `mssql-django` como backend de BD
- Correr migraciones, identificar y resolver incompatibilidades ORM
- Documentar: qué funciona directo, qué necesita ajustes, qué es imposible
- Evaluar: ¿vale la pena mantener compatibilidad dual PostgreSQL/SQL Server?

**Entregables Fase 1:**
- Ambos branches probados con datos de prueba
- Documento de decisión: qué BD cloud usar a largo plazo
- Métricas de latencia reales desde Santo Domingo

---

### FASE 2 — Modelo multi-sucursal (fundación)
> *Preparar la base de datos para operar con múltiples puntos de venta*

**2.1 App `sucursales`**
```
apps/sucursales/
    models.py      # Sucursal (codigo, nombre, direccion, activa, api_key)
    admin.py
    migrations/
```
- Modelo `Sucursal` con código único (ej: `SD-001`, `STI-001`)
- Management command `crear_sucursal` para inicialización

**2.2 Agregar `sucursal` a modelos existentes**
- `Venta.sucursal` — ForeignKey, nullable para migración gradual
- `numero_venta` con prefijo de sucursal: `SD-001-V20260414-0001`
- `ConfiguracionNegocio` deja de ser singleton (`pk=1`): una config por sucursal
  - `get_config()` filtra por `sucursal_id` del settings actual
  - El cache key cambia de `'config_negocio'` a `'config_negocio_{sucursal_id}'`
- `CierreCaja`, `Auditoria` — agregar `sucursal` FK

**2.3 Identificar la sucursal actual**
- `settings.SUCURSAL_CODIGO = 'SD-001'` en cada settings de sucursal
- `get_sucursal_actual()` helper que retorna la instancia basada en el setting
- Middleware que inyecta `request.sucursal` para usar en vistas

**Decisión clave: datos que NO llevan sucursal_id**
- Lote, MovimientoLote — son locales por naturaleza (el stock físico es de la sucursal)
- Producto, Categoría — son globales (datos maestros)
- Usuario — global (un SYSADMIN opera en todas las sucursales)

---

### FASE 3 — API REST (capa de comunicación)
> *Exponer endpoints para que las sucursales se comuniquen con la nube*

**3.1 Instalar Django REST Framework**
- `pip install djangorestframework`
- Agregar a `INSTALLED_APPS`
- Configurar autenticación: Token auth (simple) o API keys por sucursal

**3.2 Serializers para datos maestros**
```
api/serializers.py
    ProductoSerializer
    CategoriaSerializer
    ClienteSerializer
    ConfiguracionSerializer (parcial, solo campos relevantes)
```

**3.3 Endpoints de datos maestros (cloud → sucursal)**
```
GET  /api/v1/maestros/productos/?desde=<timestamp>
GET  /api/v1/maestros/categorias/?desde=<timestamp>
GET  /api/v1/maestros/clientes/?desde=<timestamp>
```
- Filtro por `fecha_modificacion > desde` para sync incremental
- Respuesta incluye `version` para control de consistencia

**3.4 Endpoints de eventos (sucursal → cloud)**
```
POST /api/v1/sync/eventos/         # Enviar batch de eventos
GET  /api/v1/sync/status/          # Estado de sincronización de la sucursal
```

**3.5 Endpoints de reportes (cloud → dashboard)**
```
GET  /api/v1/reportes/ventas-hoy/            # Todas las sucursales
GET  /api/v1/reportes/ventas-hoy/<sucursal>/
GET  /api/v1/reportes/comparativo-sucursales/
GET  /api/v1/reportes/inventario-consolidado/
```

---

### FASE 4 — Sistema de sincronización (sync engine)
> *El mecanismo que mueve datos entre sucursales y la nube*

**4.1 App `sync`**
```
apps/sync/
    models.py       # EventoSync, VersionMaestro, LogSync
    serializers.py  # Serialización de ventas/cierres para el payload
    engine.py       # SyncEngine: push eventos, pull maestros
    management/
        commands/
            sincronizar.py    # Management command para correr sync
```

**4.2 Modelo EventoSync**
```python
class EventoSync(models.Model):
    sucursal = ForeignKey(Sucursal)
    tipo_evento = CharField  # VENTA_CREADA, VENTA_ANULADA, CIERRE_CAJA
    payload = JSONField      # Datos serializados completos
    estado = CharField       # PENDIENTE → ENVIADO → CONFIRMADO / ERROR
    created_at = DateTimeField(auto_now_add)
    sent_at = DateTimeField(null)
    confirmed_at = DateTimeField(null)
    intentos = IntegerField(default=0)
    ultimo_error = TextField(blank)
    hash_payload = CharField  # Para deduplicación / idempotencia
```

**4.3 Generación de eventos (signals o explícito)**
- Opción A: `post_save` signal en Venta → crea EventoSync automáticamente
- Opción B (recomendada): llamada explícita en `procesar_venta()` después del commit
  - Más control, más predecible, más fácil de debuggear
  - El evento se crea DESPUÉS de que la transacción local sea exitosa

**4.4 SyncEngine**
```python
class SyncEngine:
    def push_eventos(self):
        """Envía eventos PENDIENTE a la API cloud"""
        eventos = EventoSync.objects.filter(estado='PENDIENTE')[:50]  # batch de 50
        for evento in eventos:
            try:
                response = requests.post(CLOUD_API_URL, json=evento.payload, headers=auth)
                if response.status_code == 200:
                    evento.estado = 'CONFIRMADO'
                    evento.confirmed_at = now()
                else:
                    evento.estado = 'ERROR'
                    evento.ultimo_error = response.text
                    evento.intentos += 1
            except ConnectionError:
                evento.ultimo_error = 'Sin conexión'
                evento.intentos += 1
            evento.save()

    def pull_maestros(self):
        """Descarga cambios en datos maestros desde la nube"""
        ultima_sync = VersionMaestro.objects.get(tabla='productos').version
        response = requests.get(f'{CLOUD_API_URL}/maestros/productos/?desde={ultima_sync}')
        for producto_data in response.json():
            Producto.objects.update_or_create(
                sku=producto_data['sku'],
                defaults=producto_data
            )

    def check_connection(self):
        """Ping a la API cloud — usado para bloquear edición de maestros offline"""
        try:
            r = requests.get(f'{CLOUD_API_URL}/ping/', timeout=3)
            return r.status_code == 200
        except:
            return False
```

**4.5 Management command `sincronizar`**
```bash
# Correr como scheduled task cada 60 segundos
python manage.py sincronizar --settings=config.settings_sucursal
```
- Loop: push_eventos → pull_maestros → sleep
- Configurable: intervalo, batch size, max retries
- Logging a `sync.log`

**4.6 Decorador `@requiere_conexion_cloud`**
- Para vistas de edición de datos maestros (productos, categorías, clientes)
- Verifica `SyncEngine.check_connection()` antes de permitir la operación
- Si offline: muestra mensaje "Cambios administrativos no disponibles sin conexión"
- La edición se envía directamente a la API cloud, no se guarda localmente primero

---

### FASE 5 — Portal administrativo cloud (React dashboard)
> *Interfaz web para el dueño del negocio*

**5.1 Proyecto React separado**
```
pos-cloud-dashboard/
    src/
        components/
            Dashboard.jsx        # Vista principal con KPIs
            VentasPorSucursal.jsx
            ComparativoChart.jsx
            ProductosEditor.jsx  # CRUD datos maestros
            SucursalesStatus.jsx # Estado de conexión de cada sucursal
        services/
            api.js              # Calls a la API REST Django
            auth.js             # Login/token
        App.jsx
    package.json
```

**5.2 Funcionalidades del portal**
- Login con credenciales Django (SYSADMIN/ADMIN)
- Dashboard: ventas del día por sucursal (con indicador de última sincronización)
- Comparativo entre sucursales: gráficas Recharts/Chart.js
- Gestión de productos: crear, editar precio, activar/desactivar (se propaga a sucursales)
- Gestión de categorías y clientes
- Estado de sucursales: última sincronización, eventos pendientes, alertas
- Reportes consolidados: reutilizar lógica de `ReporteManager` con agregación multi-sucursal

**5.3 Deployment**
- Azure Static Web Apps (gratis con cuenta educativa)
- Build: `npm run build` → deploy automático desde GitHub
- Consume la API Django (que puede estar en Azure App Service o en una VM)

**5.4 Autenticación**
- JWT tokens (djangorestframework-simplejwt)
- El React guarda el token en memory (no localStorage para seguridad)
- Refresh token flow

---

### FASE 6 — Producción multi-sucursal (integración completa)
> *Todo funcionando junto para un cliente real*

**6.1 Prueba piloto con Royal Plast**
- Sucursal principal: la actual (ya funcionando)
- "Sucursal" de prueba: segunda PC en la misma red o en la casa del dueño
- Validar: sync funciona, reportes consolidan, maestros se propagan

**6.2 Paquete de instalación multi-sucursal**
- Actualizar `instalar.bat` para preguntar: ¿sucursal nueva o nodo cloud?
- `crear_config_inicial` con código de sucursal
- `registrar_servicio.bat` ahora incluye el sync como segundo servicio

**6.3 Monitoreo**
- Endpoint `/api/v1/health/` que cada sucursal pinga
- Dashboard cloud muestra: verde (sync reciente), amarillo (>5 min), rojo (>30 min)
- Alerta por email si una sucursal lleva >1 hora sin sincronizar

---

### FASE 7+ — Horizonte futuro

**Facturación electrónica (e-CF / DGII)**
- Flag `modulo_ecf` ya existe en ConfiguracionNegocio
- Integración con Alanube o DGMax (PSFEs ya investigados)
- Cada sucursal con su propio RNC + certificado .p12

**SaaS multi-tenant**
- `django-tenants` con schema-per-tenant en PostgreSQL
- Cada "empresa" (Royal Plast, Auto Parts, etc.) es un tenant
- Portal de administración central
- Deployment: Docker → Azure Container Apps

**App móvil para el dueño**
- React Native o PWA del portal cloud
- Notificaciones push de ventas, alertas de stock, cierre de caja

---

## Branches de Git propuestos

| Branch | Propósito | Base | Dependencias |
|--------|-----------|------|--------------|
| `main` | Producción local estable | — | — |
| `develop` | Desarrollo activo (Fase 0) | main | — |
| `feature/azure-postgres` | Fase 1: BD en Neon/Azure PG | develop | — |
| `feature/azure-sql` | Fase 1: BD en Azure SQL | develop | mssql-django |
| `feature/multi-sucursal` | Fase 2: Modelo sucursales | develop | — |
| `feature/api-rest` | Fase 3: DRF endpoints | multi-sucursal | djangorestframework |
| `feature/sync-engine` | Fase 4: Sistema sync | api-rest | requests |
| `feature/cloud-dashboard` | Fase 5: React portal | api-rest | React, Recharts |

---

## Principios de ejecución

1. **Cada fase es independiente y funcional.** El sistema sigue operando local después de cada fase. La nube es un add-on, no un requerimiento.

2. **Incremental siempre.** No reescribir — agregar. Los modelos existentes reciben campos nuevos con `null=True` para migración sin fricción.

3. **Un chat por fase/módulo.** Handoff documents al final de cada sesión para mantener contexto.

4. **Probar antes de avanzar.** Cada fase tiene criterios de aceptación claros antes de empezar la siguiente.

5. **Seguridad desde el inicio.** Contraseñas en variables de entorno, SSL obligatorio, tokens con expiración, API keys por sucursal.