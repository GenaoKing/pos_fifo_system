# HANDOFF — Implementación e-CF en `pos_fifo_system`

> Estado documental: handoff historico/profundo de la fase e-CF MSeller.
> Para una lectura ejecutiva del estado actual del proyecto, ver
> `PROJECT_STATUS.md`. Para plan e-CF vivo, ver `ROADMAP_ECF_FASE_INICIAL.md`.

**Última actualización:** 10 de mayo 2026
**Autor original:** Santiago + Claude Opus 4.7 (sesiones de chat web)
**Destinatario:** próxima sesión Claude / Codex agentic / Santiago en futuro
**Estado de la implementación:** Fase Inicial — backend y flujo principal validados en development, pendiente cierre operativo de producción

---

## 1. Resumen ejecutivo

`pos_fifo_system` es un POS Django multi-tenant operando en producción para Royal Plast y otros 2 clientes. La **Ley 32-23 de Facturación Electrónica** de República Dominicana obliga a estas empresas a emitir Comprobantes Fiscales Electrónicos (e-CF) certificados por DGII.

Esta implementación integra el POS con **MSeller** como Prestador de Servicios de Facturación Electrónica (PSFE) para emitir e-CF en modalidad **Envío Diferido** (DGII permite hasta 24h entre emisión y validación), de modo que el flujo de venta del POS no espere por la latencia de DGII.

La arquitectura está diseñada para ser **agnóstica al proveedor**: en Fase 2 se podrá migrar a una librería nativa (`dgii-ecf-py`) cambiando un solo campo de configuración, sin tocar el código de ventas.

---

## 2. Estado actual

### 2.1 Lo que está terminado (código)

**Semana 0 — Pre-requisitos:**
- ✅ Fix de 3 bugs críticos en `apps/ventas/views.py`:
  - Bug 2: impresión movida fuera del `transaction.atomic` (vía `transaction.on_commit`)
  - Bug 1: validación de suma en pago mixto antes del atomic
  - Bug 3: `api_anular_venta` envuelta en `transaction.atomic` con `set_rollback(True)` para fallas FIFO
- ✅ Cuenta MSeller del cliente piloto creada
- ✅ Documentación DGII descargada y leída
- ✅ Repo `victors1681/dgii-ecf` clonado y revisado como referencia

**Semana 1 — Abstracción e infraestructura:**
- ✅ App `apps/facturacion_electronica/` creada
- ✅ Interfaz `EmisorECFInterface` definida (contrato proveedor-agnóstico)
- ✅ Dataclasses `ResultadoEmision` y `EstadoECF` (DTOs neutros)
- ✅ Clase `EstadosECF` con vocabulario de estados
- ✅ Modelos `Emisor`, `ECF`, `EventoECF` con admin Django
- ✅ Extensión de `ConfiguracionNegocio`:
  - `modulo_ecf` (boolean, ya existía)
  - `ecf_proveedor` (CharField: 'mseller' | 'nativo')
  - `emisor_activo` (FK opcional a Emisor)
  - `itbis_incluido_en_precio` (boolean, default True)
  - `itbis_porcentaje_global` (Decimal, default 18.00)
- ✅ Migraciones aplicadas

**Semana 2 — Implementación MSeller:**
- ✅ `services/mseller_http_client.py` — auth lazy, reintentos exponenciales, mapeo de errores tipados
- ✅ `services/venta_to_ecf.py` — mapper neutro Venta → dict ecf_data
- ✅ `integrations/mseller_payload.py` — dict neutro → JSON MSeller (tipos 31, 32, 34)
- ✅ `services/mseller_emisor.py` — implementa `EmisorECFInterface`
- ✅ `services/factory.py` — `get_emisor_ecf()` que selecciona implementación
- ✅ `PATCH_venta_to_ecf.md` aplicado:
  - `detalleventa_set` → `detalles`
  - Cliente CONTADO tratado como ausencia fiscal
  - normalización de RNC/Cédula en mapper

**Semana 3 — Integración con flujo de venta:**

*Bloque 1 — Refactor de servicios:*
- ✅ `apps/ventas/services/__init__.py`
- ✅ `apps/ventas/services/exceptions.py` — 9 excepciones tipadas
- ✅ `apps/ventas/services/ventas_service.py` — `procesar_venta_service()`
- ✅ `apps/ventas/services/anulaciones_service.py` — `anular_venta_service()`
- ✅ `PATCH_views_py.md` aplicado: `apps/ventas/views.py` ya delega la lógica a services

*Bloque 2 — Cola de emisión:*
- ✅ `apps/facturacion_electronica/services/cola_emision.py`

*Bloque 3 — Procesador y management command:*
- ✅ `apps/facturacion_electronica/services/procesador.py`
- ✅ `apps/facturacion_electronica/management/__init__.py`
- ✅ `apps/facturacion_electronica/management/commands/__init__.py`
- ✅ `apps/facturacion_electronica/management/commands/ecf_procesar_pendientes.py`

*Bloque 4 — Configuración:*
- ✅ `PATCH_modo_contingencia.md` aplicado como placeholder de Fase 2 (`modo_contingencia`, sin lógica activa todavía)

*Bloque 6 — UI:*
- ✅ `apps/facturacion_electronica/views.py` — endpoint AJAX de estado
- ✅ `apps/facturacion_electronica/urls.py`
- ✅ `static/js/ecf_estado.js` — componente Alpine con polling
- ✅ `PATCH_venta_exitosa.md` aplicado: badge + polling en `templates/pos/venta_exitosa.html`
- ✅ `PATCH_pos_selector_tipo_ecf.md` aplicado: selector en POS con default `32`
- ✅ `PATCH_print_manager.md` aplicado y adaptado a la arquitectura real:
  - `utils/impresoras/manager.py` prepara `venta_data['ecf']`
  - `utils/impresoras/termica.py` imprime la sección fiscal según estado

### 2.2 Lo que NO está terminado

- ❌ Configuración de Task Scheduler de Windows para correr `ecf_procesar_pendientes` cada 30s
- ❌ Tests unitarios y de integración
- ❌ Validación manual de los 3 modos de impresión con térmica real
- ❌ Onboarding del primer cliente piloto en producción

### 2.3 Lo que ya fue validado en testing development

- ✅ Mapper `venta_a_ecf_data()` con venta real tipo `32`
- ✅ Builder `build_mseller_payload()` con estructura coherente para MSeller
- ✅ Cola de emisión `encolar_emision()` creando `ECF` + `EventoECF`
- ✅ HTTP real contra TesteCF: auth, emisión y consulta
- ✅ Procesador end-to-end: `PENDIENTE -> ENVIADO -> APROBADO`
- ✅ Flujo real desde POS con badge de `venta_exitosa` mostrando `APROBADO` y `eNCF` correcto
- ✅ Smoke test posterior al fix de `validate`: la emisión real ya sale con `params=None`

---

## 3. Arquitectura

### 3.1 Vista de alto nivel

```
┌──────────────────────────────────────────────────────────────┐
│                          POS (Frontend)                      │
│                                                              │
│  Cajera elige producto → carrito → tipo e-CF (32 default)   │
│  → método pago → cierra venta                                │
│                                                              │
└──────────────────────────────┬───────────────────────────────┘
                               │ POST /ventas/procesar/
                               ▼
┌──────────────────────────────────────────────────────────────┐
│                  apps/ventas/views.py (delgado)              │
│  Solo parsea JSON y llama al service. No lógica de negocio. │
└──────────────────────────────┬───────────────────────────────┘
                               ▼
┌──────────────────────────────────────────────────────────────┐
│           apps/ventas/services/ventas_service.py             │
│                                                              │
│  procesar_venta_service():                                  │
│    1. Validaciones (carrito, total, suma pagos mixtos)      │
│    2. transaction.atomic:                                    │
│       - Validar stock                                        │
│       - Crear Venta + DetalleVenta + Pago                   │
│       - Consumir FIFO                                        │
│       - Auditoría                                            │
│       - Hooks transaction.on_commit:                         │
│         * sync_events.evento_venta_creada                    │
│         * print_manager.print_ticket_venta                   │
│         * encolar_emision (si modulo_ecf=True)               │
│    3. Retorna Venta                                          │
└──────────────────────────────┬───────────────────────────────┘
                               ▼ (post-commit, async respecto al cierre)
┌──────────────────────────────────────────────────────────────┐
│      apps/facturacion_electronica/services/cola_emision.py   │
│                                                              │
│  encolar_emision(venta, tipo_ecf):                          │
│    Crea ECF en estado PENDIENTE + EventoECF inicial         │
│    NO llama a MSeller. Retorna inmediato.                    │
└──────────────────────────────────────────────────────────────┘

  ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─
  Cola persistente en BD (tabla facturacion_electronica_ecf)
  ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─

                               ▼ (Task Scheduler cada 30s)
┌──────────────────────────────────────────────────────────────┐
│  python manage.py ecf_procesar_pendientes                    │
│                                                              │
│  Fase 1: emite ECFs en PENDIENTE/ERROR contra MSeller       │
│  Fase 2: consulta estado de ECFs en ENVIADO/EN_PROCESO      │
│                                                              │
│  Para cada ECF:                                              │
│    - select_for_update(skip_locked=True) — concurrency safe │
│    - procesar_ecf() del módulo procesador.py                 │
│    - Persiste cambios + EventoECF + intentos += 1            │
└──────────────────────────────┬───────────────────────────────┘
                               ▼
┌──────────────────────────────────────────────────────────────┐
│              MSellerEmisor (impl. concreta)                  │
│                                                              │
│  emitir(ecf_data):                                          │
│    1. _siguiente_encf() — asigna eNCF                       │
│    2. build_mseller_payload() — dict → JSON MSeller         │
│    3. http.enviar_documento() — POST /documentos-ecf        │
│    4. Mapea respuesta a ResultadoEmision                     │
│                                                              │
│  consultar_estado(encf):                                     │
│    1. http.consultar_documento(encf) — GET /documentos-ecf  │
│    2. Mapea status MSeller → EstadosECF                     │
└──────────────────────────────┬───────────────────────────────┘
                               ▼
                    ┌──────────────────┐
                    │   API MSeller    │
                    │  ecf.api.mseller │
                    └────────┬─────────┘
                             ▼
                    ┌──────────────────┐
                    │      DGII        │
                    └──────────────────┘
```

### 3.2 Estructura de archivos

```
apps/
├── ventas/
│   ├── views.py                          [delgado, delega a services]
│   ├── models.py                         [sin cambios]
│   └── services/                         [NUEVO]
│       ├── __init__.py
│       ├── exceptions.py
│       ├── ventas_service.py
│       └── anulaciones_service.py
│
├── configuracion/
│   └── models.py                         [incluye `modo_contingencia` placeholder]
│
└── facturacion_electronica/              [NUEVO toda la app]
    ├── __init__.py
    ├── apps.py
    ├── admin.py
    ├── interfaces.py                     [contratos abstractos]
    ├── models.py                         [Emisor, ECF, EventoECF]
    ├── views.py                          [endpoint AJAX estado]
    ├── urls.py
    ├── migrations/
    │   └── 0001_initial.py
    ├── services/
    │   ├── __init__.py
    │   ├── factory.py                    [get_emisor_ecf]
    │   ├── mseller_http_client.py
    │   ├── mseller_emisor.py
    │   ├── venta_to_ecf.py               [usa `venta.detalles`, maneja CONTADO]
    │   ├── cola_emision.py
    │   └── procesador.py
    ├── integrations/
    │   ├── __init__.py
    │   └── mseller_payload.py
    └── management/
        ├── __init__.py
        └── commands/
            ├── __init__.py
            └── ecf_procesar_pendientes.py

static/
└── js/
    └── ecf_estado.js                     [NUEVO — Alpine component]

templates/
├── base.html                             [ya carga `ecf_estado.js`]
└── pos/
    ├── venta_exitosa.html                [badge polling + reimpresión]
    └── punto_venta.html                  [selector tipo e-CF]

utils/
└── impresoras/
    ├── manager.py                        [inyecta datos e-CF para impresión]
    └── termica.py                        [renderiza 3 modos de impresión]

config/
├── settings.py                           [agregar logging ecf.*]
└── urls.py                               [include facturacion_electronica.urls]
```

### 3.3 Diagrama de estados de un ECF

```
                    encolar_emision()
                          │
                          ▼
                   ┌──────────────┐
                   │  PENDIENTE   │
                   └──────┬───────┘
                          │ procesador llama emitir()
                          │
        ┌─────────────────┼─────────────────┬──────────────┐
        │                 │                 │              │
        ▼                 ▼                 ▼              ▼
   ┌─────────┐      ┌──────────┐      ┌──────────┐   ┌───────┐
   │ ENVIADO │      │EN_PROCESO│      │ RECHAZADO│   │ ERROR │
   └────┬────┘      └─────┬────┘      └──────────┘   └───┬───┘
        │                 │              terminal         │
        │ procesador      │ procesador                    │ procesador
        │ consulta        │ consulta                      │ re-emite
        │                 │                               │ (intentos++)
        └────┬────────────┘                               │
             │                                            │
             ├──────────────────┬───────────────────┐     │
             │                  │                   │     │
             ▼                  ▼                   ▼     ▼
        ┌──────────┐    ┌────────────────┐    ┌──────────┐
        │ APROBADO │    │ APROBADO_COND. │    │ RECHAZADO│
        └──────────┘    └────────────────┘    └──────────┘
         terminal ✓        terminal ✓✗          terminal ✗
```

**Estados terminales:** APROBADO, APROBADO_CONDICIONAL, RECHAZADO. El procesador no los toca.

**Estados reintentables:** PENDIENTE, ENVIADO, EN_PROCESO, ERROR. El procesador los avanza, hasta `intentos >= 5` que los deja en ERROR sin más reintentos.

---

## 4. Configuración requerida

### 4.1 Variables de entorno (NSSM service)

Por cada cliente del POS, se definen 3 variables sufijadas con el código del cliente:

```bash
# Royal Plast (ejemplo)
MSELLER_EMAIL_ROYAL=usuario@royalplast.do
MSELLER_PASSWORD_ROYAL=********
MSELLER_API_KEY_ROYAL=eyJraWQiOiJM...

# Cliente auto partes (otro ejemplo)
MSELLER_EMAIL_AUTOPARTES=usuario@autopartes.do
MSELLER_PASSWORD_AUTOPARTES=********
MSELLER_API_KEY_AUTOPARTES=eyJraWQiOiJM...
```

Estas variables se referencian (no se almacenan) desde `Emisor.config_proveedor`:

```json
{
  "email_env": "MSELLER_EMAIL_ROYAL",
  "password_env": "MSELLER_PASSWORD_ROYAL",
  "api_key_env": "MSELLER_API_KEY_ROYAL",
  "entorno": "TesteCF",
  "validar_antes_enviar": false
}
```

**Valores válidos para `entorno`:**
- `TesteCF` — sandbox de pruebas (cualquier secuencia eNCF funciona)
- `CerteCF` — certificación con DGII (proceso formal)
- `eCF` — producción

### 4.2 LOGGING en `settings.py`

Agregar a la sección LOGGING:

```python
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{asctime} {levelname} [{name}] {message}',
            'style': '{',
        },
    },
    'handlers': {
        # ... handlers existentes ...
        'ecf_file': {
            'level': 'DEBUG',
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': BASE_DIR / 'logs' / 'ecf.log',
            'maxBytes': 10 * 1024 * 1024,  # 10 MB
            'backupCount': 5,
            'formatter': 'verbose',
        },
    },
    'loggers': {
        # ... loggers existentes ...
        'ecf': {
            'handlers': ['ecf_file', 'console'],
            'level': 'INFO',
            'propagate': False,
        },
        'ecf.mseller': {
            'handlers': ['ecf_file', 'console'],
            'level': 'INFO',
            'propagate': False,
        },
        'ecf.procesador': {
            'handlers': ['ecf_file', 'console'],
            'level': 'INFO',
            'propagate': False,
        },
        'ecf.cola': {
            'handlers': ['ecf_file', 'console'],
            'level': 'INFO',
            'propagate': False,
        },
        'ecf.views': {
            'handlers': ['ecf_file', 'console'],
            'level': 'INFO',
            'propagate': False,
        },
        'ventas.service': {
            'handlers': ['ecf_file', 'console'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}
```

### 4.3 Setup en admin Django

Pasos mínimos para activar e-CF para una sucursal:

1. **Crear Emisor** (Admin → Facturación Electrónica → Emisores):
   - RNC: 9 u 11 dígitos sin guiones
   - Razón social: nombre legal
   - Proveedor actual: `mseller`
   - `config_proveedor`: el JSON de arriba con env vars
   - Activo: `True`

2. **Configurar la sucursal** (Admin → Configuración → Configuración del Negocio):
   - Marcar `modulo_ecf = True`
   - `ecf_proveedor = mseller`
   - Asignar el Emisor recién creado a `emisor_activo`
   - Verificar `itbis_incluido_en_precio` (default True para Royal Plast)
   - Verificar `itbis_porcentaje_global = 18.00`

3. **Configurar Task Scheduler** (Windows):
   - Programa: `python manage.py ecf_procesar_pendientes`
   - Cada 30 segundos
   - Working directory: el del proyecto
   - Usar la cuenta del servicio NSSM para que herede env vars

### 4.4 URLs principales del proyecto

Agregar a `config/urls.py`:

```python
urlpatterns = [
    # ... rutas existentes ...
    path(
        'facturacion-electronica/',
        include('apps.facturacion_electronica.urls'),
    ),
]
```

### 4.5 Static y templates

`base.html` ya carga `ecf_estado.js`, por lo que el badge Alpine de estado e-CF queda disponible para `venta_exitosa.html` sin wiring adicional.

---

## 5. Decisiones arquitectónicas — el "por qué"

### 5.1 Cola persistente vs sync con on_commit

**Decisión:** la venta crea ECF en estado PENDIENTE; el management command lo procesa en background.

**Alternativas descartadas:**
- Sync con `on_commit`: la cajera espera el round-trip MSeller (~2s típico, picos de 30s+). Inaceptable.
- Threading desde `on_commit`: frágil con Waitress single-worker; thread puede morir si el proceso recicla.

**Trade-off aceptado:** hasta 30s de delay entre venta y emisión. Aceptable porque DGII permite Envío Diferido (24h de plazo).

### 5.2 Agnóstico al proveedor

**Decisión:** `EmisorECFInterface` define un contrato; las implementaciones concretas (MSeller hoy, nativo en Fase 2) viven en `services/`. El factory `get_emisor_ecf()` selecciona según `ConfiguracionNegocio.ecf_proveedor`.

**Razón:** MSeller cobra por documento; en Fase 2 vamos a evaluar implementación nativa con la librería `dgii-ecf-py` (cuenta propia). Cambiar de proveedor debe ser un cambio de configuración, no de código.

### 5.3 Mapper en dos pasos: Venta → dict neutro → JSON proveedor

**Decisión:** `venta_to_ecf.py` produce un `dict` neutro; `mseller_payload.py` lo transforma al JSON específico de MSeller.

**Razón:** la lógica fiscal (desglose ITBIS, indicadores, totales) es la misma para cualquier proveedor — DGII no cambia de schema. Solo cambia el nombre y formato de los campos del JSON. Esta separación reusa toda la lógica fiscal en Fase 2 con la nativa.

### 5.4 Cliente CONTADO genérico = ausencia de cliente

**Decisión:** si `Cliente.tipo == 'CONTADO'`, el mapper lo trata como `None` para fines de e-CF.

**Razón:** el Cliente CONTADO genérico es un placeholder operativo del POS, no una identidad fiscal. DGII acepta omitir la sección Comprador en e-CF tipo 32, así que omitimos en lugar de enviar datos ficticios.

### 5.5 NC tipo 34 solo si ECF original APROBADO

**Decisión:** al anular una venta, solo se emite NC tipo 34 si el ECF original llegó a APROBADO/APROBADO_CONDICIONAL. Si está PENDIENTE/ERROR/RECHAZADO, no se emite NC.

**Razón:** la NC sirve para "rectificar" un documento que DGII ya tiene. Si DGII nunca recibió el ECF original (estaba en cola), no hay nada que rectificar. El procesador detecta `venta.estado == 'ANULADA'` (vía `debe_abortar_ecf_pendiente()`) y aborta el ECF original sin enviarlo.

### 5.6 Persistencia de `xml_firmado` con el JSON enviado

**Decisión:** el campo `ECF.xml_firmado` guarda el payload JSON que se envió a MSeller, no el XML firmado real.

**Razón:** MSeller no expone un endpoint público para descargar el XML firmado. El JSON enviado es la evidencia fiscalmente equivalente que tenemos del lado del POS. La Ley exige conservación 10 años; MSeller retiene el XML firmado por su lado.

**Limitación:** si en el futuro migramos de PSFE, perdemos acceso al XML firmado original. Mitigación: cuando hagamos la nativa en Fase 2, sí guardaremos el XML real con firma `.p12`.

### 5.7 Tres modos de impresión según estado del ECF

**Decisión:** el ticket térmico imprime de forma distinta según `ECF.estado`:
- **APROBADO/APROBADO_CONDICIONAL**: RI completa con eNCF, QR, código de seguridad, fecha de firma — válido fiscalmente
- **PENDIENTE/ENVIADO/EN_PROCESO con datos**: RI con leyenda obligatoria DGII "e-CF emitido en modalidad Envío Diferido"
- **PENDIENTE sin eNCF todavía**: leyenda "e-CF en proceso, reimprima en unos minutos"
- **RECHAZADO/ERROR**: leyenda destacada "DOCUMENTO RECHAZADO POR DGII — NO VÁLIDO"

**Razón:** DGII exige la leyenda Envío Diferido cuando la RI se entrega antes de validación (lo que es el flujo normal del POS dado que MSeller responde async).

### 5.8 Selector de tipo en POS, default 32

**Decisión:** el cajero elige el tipo de e-CF antes de cerrar la venta. Selector de 2 opciones: Consumo (32) por default, Crédito Fiscal (31) si el cliente lo pide.

**Alternativa descartada:** inferencia automática (si cliente tiene RNC → 31). Genera confusión: el cliente puede tener RNC pero no querer crédito fiscal. Mejor explícito.

**El tipo 34 (NC) NO se elige desde el POS** — se dispara automáticamente al anular una venta con ECF aprobado.

### 5.9 ITBIS configurable a nivel negocio

**Decisión:** `ConfiguracionNegocio` tiene dos campos:
- `itbis_incluido_en_precio` (boolean)
- `itbis_porcentaje_global` (Decimal, default 18.00)

**Razón:** Royal Plast tiene precios al consumidor que ya incluyen ITBIS (back-calculo: base = precio / 1.18). Otros clientes podrían usar precios base sin ITBIS. La configuración por negocio cubre ambos casos sin agregar campo a `Producto` por ahora.

**TODO Fase 2:** agregar `Producto.itbis_pct` para tasas diferenciadas (16%, exento). El helper `_get_itbis_pct()` ya tiene comentario con dónde modificar.

### 5.10 Decisiones acumuladas que conviene preservar

- **eNCF se asigna en el procesador, no al encolar.** La cola solo persiste intención (`ECF` en `PENDIENTE`). La asignación real del eNCF ocurre al emitir en `MSellerEmisor`. En `TesteCF` esto es suficiente; en producción quedará pendiente integrar consulta/control de rangos DGII como TODO de Semana 4.

- **`codigo_seguridad` llega en la respuesta inmediata de emisión.** Esto permite imprimir QR + código desde la primera respuesta de MSeller, incluso antes de que DGII confirme estado terminal.

- **El selector POS no solo defaultea a `32`, también valida visualmente el `31`.** Si el cajero elige tipo `31` sin cliente con RNC/Cédula válido, el frontend bloquea la operación antes de enviar.

- **`modo_contingencia` existe solo como placeholder de Fase 2.** El campo ya está en configuración, pero no activa ninguna lógica especial en la Fase Inicial.

- **El patch de impresión se adaptó a la arquitectura real del proyecto.** En vez de mover toda la lógica a `manager.py`, se decidió que `manager.py` solo enriquezca `venta_data` y que `termica.py` siga siendo quien renderiza el ticket. Esto preserva la separación actual de responsabilidades.

- **`validate=true` NO forma parte del flujo normal de emisión.** Tras las pruebas contra TesteCF se decidió que la emisión real siempre llame a MSeller con `validar=False`. El modo `validate=true` queda reservado para debugging/manual testing porque en sandbox puede retornar un documento completo y no se comporta como un dry-run puro.

---

## 6. Convenciones del proyecto que deben respetarse

### 6.1 Patrones del codebase existente (ver `userMemories`)

- **Modales:** usar inline styles `position: fixed; top:0; left:0; right:0; bottom:0; z-index:9999`. NO usar Tailwind positioning classes. Backdrop `rgba(0,0,0,0.6)` + `backdrop-filter:blur(6px)`.
- **UI feedback:** siempre `showToast(type, msg)` y `showConfirm(titulo, mensaje, opts)` desde `static/js/utils.js`. Nunca `alert()` o `confirm()` nativos.
- **CSRF/fetch:** usar `getCsrfToken()` y `jsonHeaders()` globales de `utils.js`. No duplicar lógica CSRF.
- **Django→Alpine:** usar `json_script` para datos hidratados desde el view.
- **Tailwind:** clases del design system (`card`, `card-body`, `btn-primary`, `btn-outline`, `form-input`, `form-label`, `badge-success`, etc.). Raw utilities solo para layout (`grid`, `flex`, `gap`).
- **Alpine:** función nombrada con `x-data="miApp()"`, siempre `init()` como entry point.
- **Templates:** extender `base.html`, usar `{% block breadcrumb %}` y `{% block content %}`.

### 6.2 Patrones específicos del nuevo código (e-CF)

- **Validación previa al `transaction.atomic`** cuando es posible (validaciones de input puro). Evita rollback innecesario.
- **`transaction.on_commit(lambda v=venta: ...)`** con captura por valor para evitar problemas de closure.
- **Excepciones tipadas** en `apps/ventas/services/exceptions.py` con `status_code` mapeable a HTTP. El view captura `ErrorVentaBase` y arma JsonResponse.
- **Procesador como función pura** (`procesar_ecf(ecf)`), no método de clase. Testeable sin estado.
- **`select_for_update(skip_locked=True)`** en el management command para concurrency.
- **Logging por dominio:** `ecf.mseller`, `ecf.procesador`, `ecf.cola`, `ecf.views`, `ventas.service`.

### 6.3 Reglas de oro DGII (para no equivocarse)

1. **`MontoItem` en e-CF es base SIN ITBIS.** El ITBIS se reporta aparte en `TotalITBIS`. Si tu input tiene ITBIS incluido, hay que back-calcular.
2. **eNCF formato exacto:** `E + tipo(2) + secuencia(10)` = 13 caracteres. Ej: `E320000000123`.
3. **Suma de pagos en mixto debe cuadrar al céntimo** con el total, o DGII rechaza.
4. **Cliente con RNC para tipo 31, opcional para tipo 32.** Tipo 31 sin RNC válido = rechazo automático.
5. **Tipo 34 (NC) requiere `InformacionReferencia`** con `NCFModificado` (eNCF original), `CodigoModificacion` (1=anulación, 2=texto, 3=monto), `RazonModificacion`.
6. **Si DGII rechaza, ese eNCF NO se reutiliza.** Hay que emitir uno nuevo con secuencia distinta.
7. **Envío Diferido permite hasta 24h** entre emisión y validación. La RI debe llevar la leyenda obligatoria.
8. **Documentos > RD$ 250,000 (tipo 32) van como "documento extendido"**, no resumen. MSeller lo decide automáticamente.
9. **Solo facturas no electrónicas (serie B) en contingencia notificada a DGII.** No se puede facturar en papel sin notificación previa.
10. **Conservación 10 años** del XML firmado. MSeller lo retiene; nuestro POS guarda el JSON enviado como evidencia local.

---

## 7. Smoke test ejecutado

La validación end-to-end contra TesteCF ya se ejecutó en development. El detalle operativo y los resultados concretos quedaron documentados en `docs/historico/TESTING_ECF_2026-05-09.md`.

### 7.1 Resultado general

- Se validaron capas aisladas (mapper, payload, cola).
- Se validó HTTP real contra MSeller TesteCF.
- Se validó procesador end-to-end hasta `APROBADO`.
- Se validó el flujo real desde POS hasta `venta_exitosa`.
- Se identificó y corrigió la ambigüedad de `validate=true` en el flujo normal.

### 7.2 Pre-requisitos

- ✅ Todos los archivos creados y patches aplicados
- ✅ Migraciones corridas
- ✅ Variables de entorno configuradas
- ✅ Emisor creado en admin con `entorno=TesteCF`
- ✅ `ConfiguracionNegocio.modulo_ecf=True` y `emisor_activo` apuntando al Emisor

### 7.3 Script de referencia del smoke test

```python
# manage.py shell

from apps.ventas.models import Venta
from apps.facturacion_electronica.services.cola_emision import encolar_emision
from apps.facturacion_electronica.services.procesador import procesar_ecf
from apps.facturacion_electronica.models import ECF

# 1. Tomar una venta real existente
venta = Venta.objects.filter(estado='COMPLETADA').first()

# 2. Encolar manualmente (simula el hook post-commit)
ecf = encolar_emision(venta=venta, tipo_ecf='32')
print(f'ECF encolado: {ecf.id}, estado: {ecf.estado}')
# Esperado: estado=PENDIENTE

# 3. Procesar manualmente
ecf.refresh_from_db()
resultado = procesar_ecf(ecf)
print(resultado)
# Esperado: estado_nuevo=ENVIADO o EN_PROCESO

# 4. Verificar que MSeller asignó eNCF y código de seguridad
ecf.refresh_from_db()
print(f'eNCF: {ecf.encf}')
print(f'Código seguridad: {ecf.codigo_seguridad}')
print(f'Track ID: {ecf.track_id}')

# 5. Esperar ~30s y consultar estado
import time
time.sleep(30)
resultado = procesar_ecf(ecf)
print(resultado)
# Esperado: estado_nuevo=APROBADO

# 6. Ver eventos
for evento in ecf.eventos.order_by('fecha'):
    print(f'{evento.fecha}: {evento.estado_anterior} → {evento.estado_nuevo}: {evento.mensaje}')
```

### 7.4 Casos cubiertos / sugeridos

1. **Venta normal con cliente CONTADO** → tipo 32, sin sección Comprador en payload
2. **Venta con cliente real con RNC** → tipo 32, con Comprador
3. **Venta con cliente con RNC, eligiendo tipo 31** → debería emitirse OK
4. **Venta con cliente sin RNC, intentando tipo 31** → `TipoECFInvalidoError` antes de encolar
5. **Anulación de venta con ECF aprobado** → debe emitir NC tipo 34
6. **Anulación de venta con ECF en PENDIENTE** → no emite NC; el procesador aborta el ECF original
7. **Pago mixto con suma incorrecta** → `PagoMixtoInconsistenteError` antes de encolar
8. **MSeller caído (variables de entorno inválidas a propósito)** → el ECF queda en ERROR y se reintenta hasta 5 veces

---

## 8. Hacia dónde va la implementación (roadmap)

### 8.1 Fase Inicial (en curso)

- ✅ Semana 0-3 — código completo
- ✅ Semana 3-4 — smoke test y testing/debugging ejecutados en development
- 🔜 Onboarding del primer cliente piloto en producción
- 🔜 Estabilización (~2-3 semanas en producción real con MSeller)

### 8.2 Fase 2 — Mejoras (3-6 meses post-piloto)

**a. Librería nativa `dgii-ecf-py`:**
- Crear repo separado `dgii-ecf-py` (no toca `pos_fifo_system`)
- Implementación de XML + firma `.p12` + envío directo a DGII
- Una vez estable, agregar implementación `NativoEmisor(EmisorECFInterface)` en `apps/facturacion_electronica/services/`
- Migrar clientes uno por uno cambiando `Emisor.proveedor_actual = 'nativo'`
- MSeller queda como fallback

**b. Modo contingencia funcional:**
- Activar la lógica del campo `modo_contingencia` (ya existe como placeholder)
- Emisión con NCF papel (serie B) cuando DGII está caída +24h
- Comando que convierte NCF papel a e-CF al volver el servicio
- UI en admin para que el dueño active/desactive con un toggle

**c. ITBIS por producto:**
- Agregar campo `Producto.itbis_pct` (Decimal, nullable, default None)
- Modificar `_get_itbis_pct()` en `venta_to_ecf.py` para leerlo cuando exista
- Soporte para tasas 16% y exento por producto

**d. Vehicle catalog (cliente auto partes):**
- Importar dataset Kaggle de modelos de autos a PostgreSQL
- Decoder offline VIN con `vininfo`
- Asociación VIN → cliente en venta (campo opcional en e-CF)

**e. Hardening operativo:**
- Dashboard de métricas en admin: ECFs aprobados/rechazados/error por día
- Alertas por email al SYSADMIN cuando hay >5 ECFs en ERROR
- Comando de reconciliación: detecta ventas sin ECF asociado y las encola

**f. Performance:**
- Batch consulta de estados (MSeller permite hasta 100 eNCFs por request)
- Mover de LocMemCache a Redis si se escala a >1 worker Waitress

### 8.3 Fase 3 — SaaS multi-tenant (6-12 meses)

- Migración de Royal Plast + clientes a Azure (ver `ROADMAP_CLOUD.md`)
- Multi-tenancy con `django-tenants` (schema-per-tenant)
- Onboarding self-service para nuevos clientes
- Modelo de negocio SaaS con cobro mensual por sucursal/usuario

---

## 9. Glosario

| Término | Significado |
|---|---|
| **e-CF** | Comprobante Fiscal Electrónico (lo que emite DGII) |
| **eNCF** | Número de Comprobante Fiscal Electrónico (formato `E + tipo + 10 dígitos`) |
| **NCF** | Número de Comprobante Fiscal (papel, serie B, no electrónico) |
| **DGII** | Dirección General de Impuestos Internos (autoridad fiscal RD) |
| **PSFE** | Prestador de Servicios de Facturación Electrónica (ej: MSeller) |
| **MSeller** | PSFE certificado por DGII que usamos en Fase Inicial |
| **RNC** | Registro Nacional de Contribuyentes (9-11 dígitos, identidad fiscal) |
| **Tipo 31** | Factura de Crédito Fiscal Electrónica (B2B, requiere RNC) |
| **Tipo 32** | Factura de Consumo Electrónica (B2C, RNC opcional) |
| **Tipo 34** | Nota de Crédito Electrónica (rectifica un ECF previo) |
| **RI** | Representación Impresa (la versión papel del e-CF) |
| **ITBIS** | Impuesto sobre Transferencias de Bienes Industrializados y Servicios (IVA dominicano) |
| **Envío Diferido** | Modalidad DGII que permite hasta 24h entre emisión y validación |
| **Contingencia** | Estado de excepción notificado a DGII cuando no se puede emitir e-CF |
| **TesteCF** | Sandbox de MSeller para pruebas |
| **CerteCF** | Entorno de certificación con DGII |
| **eCF** | Entorno de producción |

---

## 10. Notas para Codex (próxima sesión agentic)

Si esta sesión se transfiere a Codex u otro agente, puntos clave para mantener coherencia:

### 10.1 Antes de modificar código

1. **Leer este handoff completo** + `ROADMAP_ECF_FASE_INICIAL.md`
2. **Tomar la sección 2 como source of truth** de qué ya quedó aplicado y qué sigue pendiente en el repo actual
3. **Verificar que las migraciones corrieron** con `python manage.py showmigrations facturacion_electronica configuracion`

### 10.2 Patrón de trabajo

- **El usuario prefiere diff-style targeted changes**, no rewrites de archivos completos
- **Documentar el código** con docstrings explicando el "por qué" además del "qué"
- **Validar decisiones arquitectónicas** antes de implementar (no hacer asunciones grandes sin confirmar)
- **Preguntar antes de tocar archivos no relacionados** al scope de la tarea

### 10.3 Cosas a NO hacer

- ❌ Acoplar `apps/ventas` a `apps/facturacion_electronica` directamente. La integración va vía hooks `transaction.on_commit` con captura de excepciones.
- ❌ Romper el contrato `EmisorECFInterface`. Si necesitás cambios, agregalos como métodos nuevos opcionales primero, deprecar el viejo después.
- ❌ Asumir que MSeller responde rápido. El procesador y todos los hooks deben ser tolerantes a latencias de 30s+.
- ❌ Saltarse el patrón de excepciones tipadas. Si agregás un error nuevo, va en `apps/ventas/services/exceptions.py` con su `status_code`.
- ❌ Hardcodear configuración de un cliente específico. Todo debe leerse de `ConfiguracionNegocio` o `Emisor.config_proveedor`.

### 10.4 Comandos útiles

```bash
# Estado de la cola
python manage.py shell -c "
from apps.facturacion_electronica.models import ECF
from collections import Counter
print(Counter(ECF.objects.values_list('estado', flat=True)))
"

# Procesar cola manualmente (debug)
python manage.py ecf_procesar_pendientes --dry-run
python manage.py ecf_procesar_pendientes --solo-emitir --limite 5
python manage.py ecf_procesar_pendientes --ecf-id 42

# Ver eventos de un ECF específico
python manage.py shell -c "
from apps.facturacion_electronica.models import ECF
ecf = ECF.objects.get(id=42)
for e in ecf.eventos.order_by('fecha'):
    print(f'{e.fecha}: {e.estado_anterior} -> {e.estado_nuevo}: {e.mensaje}')
"

# Reintentar un ECF en ERROR (resetea intentos)
python manage.py shell -c "
from apps.facturacion_electronica.models import ECF
from apps.facturacion_electronica.interfaces import EstadosECF
ecf = ECF.objects.get(id=42)
ecf.estado = EstadosECF.PENDIENTE
ecf.intentos = 0
ecf.save()
"
```

---

## 11. Bugs conocidos / pendientes

*(Esta sección se llenará durante la sesión de testing/debugging)*

### 11.1 Bugs identificados durante implementación

- **`Venta.puede_anularse()` (apps/ventas/models.py)**: bug pre-existente al comparar timezones. `datetime.now()` retorna naive, `fecha_limite` está en zona Santo Domingo. Funciona en la práctica por la ventana de 15 días pero técnicamente incorrecto. **Fuera de scope e-CF**, pendiente cleanup.

- **`MSellerEmisor.descargar_xml_aprobado()`**: levanta `NotImplementedError` por diseño — MSeller no expone endpoint público. Confirmar con soporte MSeller si esto puede mejorarse.

- **`MSellerEmisor.consultar_estado(track_id)`**: el parámetro se llama `track_id` por neutralidad de la interfaz, pero MSeller consulta por eNCF. El caller debe pasar `ecf.encf`. **TODO refactor**: renombrar a `identificador` en próxima revisión de la interfaz.

### 11.2 Bugs encontrados en testing/debugging

- **Ambigüedad de `validate=true` en TesteCF**: durante las pruebas se observó que `?validate=true` puede retornar un documento completo y no comportarse como un dry-run puro. **Resuelto a nivel de diseño actual**: `MSellerEmisor.emitir()` ahora fuerza `validar=False` en el flujo normal, y `validate=true` queda reservado para debugging/manual testing.

- **Tipo `31` fallando por orden de `Comprador` en el payload**: en una prueba con cliente con RNC, DGII devolvió error de XML inválido indicando `Encabezado` con hijo `Comprador` no esperado. La causa fue el orden de serialización del `Encabezado` en `build_mseller_payload()` (`Comprador` quedaba después de `Totales`). **Resuelto**: el builder ahora arma el orden `Version -> IdDoc -> Emisor -> Comprador -> Totales`.

- **Tipo `31` también sensible al orden interno de `IdDoc`**: tras corregir `Comprador`, una nueva prueba devolvió error de XML inválido indicando `IdDoc` con hijo `FechaVencimientoSecuencia` fuera de lugar. La causa fue que `_build_id_doc()` agregaba esa clave al final por inserción incremental. **Resuelto**: `_build_id_doc()` ahora construye el dict de tipo `31` completo en el orden esperado por MSeller/DGII.

- **Tipo `31` también sensible al orden interno de `Comprador`**: una prueba posterior devolvió rechazo indicando `Comprador` con hijo `RazonSocialComprador` fuera de lugar y esperando `RNCComprador` primero. **Resuelto**: `_build_comprador()` ahora serializa en orden `RNCComprador -> RazonSocialComprador -> DireccionComprador`.

- **Reemisión del mismo ECF no actualizaba al intento vigente**: durante troubleshooting de tipo `31`, el modelo `ECF` seguía mostrando el primer `encf` aunque ya existía una segunda secuencia generada. La causa fue que `_aplicar_resultado_emision()` solo persistía `encf`/`codigo_seguridad` si los campos estaban vacíos. **Resuelto**: el procesador ahora sobrescribe `encf`, `track_id`, `codigo_seguridad`, `xml_firmado` y `xml_respuesta` con la respuesta del intento actual.

- **Rechazo por secuencia ya utilizada tras varios reintentos manuales en dev**: durante el troubleshooting de tipo `31`, un intento posterior recibió `Este número de secuencia ya ha sido utilizado.` para `E310000000002`. La lectura más probable es una desalineación temporal entre la secuencia ya usada remotamente y la última persistida localmente, causada por los reintentos sobre el mismo `ECF` mientras se corregía el tracking del `encf`. **Interpretación**: artefacto de pruebas en development, no comportamiento esperado del flujo limpio.

- **Tipo `31` sensible a la fecha real de vencimiento de secuencia**: una prueba limpia posterior devolvió `Fecha de vencimiento de secuencia inválida.` La inferencia automática `31-12-año_actual` no resultó válida para ese emisor/entorno. **Ajuste realizado**: `build_mseller_payload()` ahora soporta `fecha_vencimiento_secuencia` configurable desde `Emisor.config_proveedor`, inyectada por `MSellerEmisor`.

- **Tipo `31` requiere mayor alineación con el ejemplo oficial de MSeller**: tras compartir el ejemplo JSON esperado, se extendió la inyección de configuración para soportar `indicador_envio_diferido`, `tipo_ingresos`, `tipo_pago` y `fecha_limite_pago`, y se agregó `Paginacion` + `FechaHoraFirma` + campos explícitos de totales para acercar el payload del builder al contrato mostrado por MSeller.

- **Tipo `31` también sensible al orden interno de `Totales`**: una prueba posterior devolvió rechazo indicando `Totales` con hijo `MontoExento` fuera de lugar. La causa fue que `_build_totales()` estaba serializando `ITBIS1` antes de `MontoExento`. **Resuelto**: `_build_totales()` ahora sigue un orden más alineado con el ejemplo oficial de MSeller para tipo `31`.

- **`FechaHoraFirma=""` rechazado por DGII en tipo `31`**: tras acercar el payload al ejemplo oficial, una prueba nueva devolvió `El campo FechaHoraFirma de la sección FechaHoraFirma no es válido.` **Resuelto**: se dejó de enviar `FechaHoraFirma` vacío; el builder ahora omite ese campo para tipo `31`.

- **Estrategia actual de troubleshooting para tipo `31`: payload mínimo viable**: tras varios rechazos estructurales y de validación, el builder se simplificó para `31` removiendo `Paginacion`, `TotalPaginas`, `MontoNoFacturable` y `MontoExento=0` cuando no aplica. Se confirmó además que `IndicadorEnvioDiferido` debe permanecer en `1` para `31`: una prueba con `0` fue rechazada explícitamente por DGII, así que el builder volvió a default `1` para ese tipo.
- **Ajuste posterior de `IndicadorMontoGravado` en tipo `31`**: a partir de una referencia encontrada en el portal DGII, se concluyó que cuando `MontoItem` representa base gravable sin ITBIS incluido, `Encabezado.IdDoc.IndicadorMontoGravado` debe enviarse en `0`. El builder de tipo `31` volvió a incluirlo explícitamente con ese valor.
- **Tipo `31` finalmente validado en TesteCF**: una emisión posterior fue aceptada por DGII con `encf=E310000000013`. La combinación que destrabó el flujo fue:
  - `FechaVencimientoSecuencia=31-12-2028`
  - `IndicadorEnvioDiferido=1`
  - `IndicadorMontoGravado=0`
  - `TipoIngresos=01`
  - `TipoPago=1`
  - payload minimalista sin `Paginacion`, `TotalPaginas` ni `MontoExento` cuando no aplica

- **Lectura importante sobre la consulta pública DGII en rechazos**: durante pruebas fallidas de tipo `31`, el portal mostraba `Razón social comprador = -` y `Total de ITBIS = -` aunque esos datos sí estaban presentes en el XML firmado. Esto sugiere que la consulta pública de documentos rechazados puede mostrar una vista parcial y no debe usarse como evidencia definitiva de ausencia de campos en el payload.

- **`validate=true` documentado por MSeller vs comportamiento observado en TesteCF**: aunque MSeller lo describe como validación previa sin generación real, en pruebas del sandbox la respuesta siguió incluyendo `ecf`, `internalTrackId`, `securityCode`, `qr_url` y `signedDate`. **Interpretación actual**: no usarlo en el flujo normal, pero reconocer que en TesteCF devuelve metadata suficiente para el troubleshooting.

- **Transición visual en vivo del badge no observada todavía**: en la prueba GUI el badge cargó ya resuelto en `APROBADO`, por lo que quedó validado el estado final y el polling, pero no una transición visual real desde estado intermedio. No bloquea producción inicial.

---

## 12. Referencias externas

### 12.1 Documentación MSeller

- https://docs.ecf.mseller.app/docs/integration/overview
- https://docs.ecf.mseller.app/docs/integration/authentication
- https://docs.ecf.mseller.app/docs/integration/document-format
- https://docs.ecf.mseller.app/docs/integration/documents
- https://docs.ecf.mseller.app/docs/integration/document-queries
- https://docs.ecf.mseller.app/docs/integration/formato-documentos-ecf
- https://docs.ecf.mseller.app/docs/resources/best-practices

### 12.2 Documentación DGII

- Informe Técnico e-CF v1.0:
  https://dgii.gov.do/cicloContribuyente/facturacion/comprobantesFiscalesElectronicosE-CF/Documentacin%20sobre%20eCF/Informe%20y%20Descripci%C3%B3n%20T%C3%A9cnica/Informe%20T%C3%A9cnico%20e-CF%20v1.0.pdf
- Modelos Ilustrativos de RI:
  https://dgii.gov.do/cicloContribuyente/facturacion/comprobantesFiscalesElectronicosE-CF/Documentacin%20sobre%20eCF/Informe%20y%20Descripci%C3%B3n%20T%C3%A9cnica/Representaci%C3%B3n%20Impresa%20(Modelos%20ilustrativos).pdf
- Preguntas Frecuentes Generales:
  https://dgii.gov.do/cicloContribuyente/facturacion/comprobantesFiscalesElectronicosE-CF/Preguntas%20frecuentes/Generales/Preguntas%20Frecuentes%20e-CF%20Generales%20.pdf
- Ley 32-23 + Decreto 587-24

### 12.3 Repos de referencia

- https://github.com/victors1681/dgii-ecf — librería Python no oficial, base para la nativa Fase 2

### 12.4 Documentos del proyecto

- `ROADMAP_ECF_FASE_INICIAL.md` — roadmap original de la fase
- `arquitectura_completa.html` — diagrama de arquitectura (puede estar desactualizado respecto a este handoff)
- `directorio_proyecto` — estructura de carpetas
- `ROADMAP_CLOUD.md` — plan Azure/SaaS para Fase 3
- `TEMPLATE_STANDARDS.md` — convenciones de templates Django/Alpine
