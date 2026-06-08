# POS FIFO System — e-CF Fase Inicial
## Integración MSeller (cumplimiento) + Kickoff librería nativa (proyecto paralelo)

**Fecha:** Abril 2026
**Duración estimada:** 4-6 semanas calendario para un dev part-time (equivalente a ~3-4 semanas full-time)
**Estado de partida:** Certificado `.p12` obtenido, acceso a portal de precertificación DGII solicitado, clientes operando con NCF papel
**Estrategia:** Cumplimiento operativo inmediato vía PSFE (MSeller), librería Python nativa construida en paralelo como proyecto separado con mentalidad OSS desde el día 1

---

## Contexto y decisiones tomadas

Durante el análisis previo se evaluaron cuatro caminos y se eligió el camino híbrido:

1. Integración con MSeller (PSFE certificado por DGII, tier gratuito 200 docs/mes, planes pagos baratos para volumen) cubre cumplimiento legal y operativo en 2-4 semanas.
2. Librería Python nativa `dgii-ecf-py` se desarrolla en paralelo en repositorio separado, privado en su fase inicial pero diseñado desde el día 1 como código pensado para OSS futuro bajo licencia MIT.
3. Las dos piezas se integran al POS detrás de una interfaz común (`EmisorECFInterface`) para que cuando la nativa esté lista, la migración sea swap de implementación, no rewrite.
4. La librería Node `victors1681/dgii-ecf` (MIT) sirve como referencia arquitectónica y banco de pruebas — se lee para entender, se reproduce con verificación cruzada en Python, no se ejecuta en runtime.

**Lo que NO se hace en esta fase:** set de pruebas con DGII, certificación, emisión nativa en producción. Eso es Fase 2.

---

## Pre-requisitos (Semana 0 — antes de empezar Fase Inicial)

Estas tareas son chequeos que cuestan días si los descubrís tarde. No empezar Semana 1 sin tenerlas resueltas.

**0.1 Fix de bugs críticos en `apps/ventas/views.py`**

Estos bugs ya identificados se vuelven inaceptables al integrar e-CF, porque los efectos se propagan a registros fiscales con DGII. Fix obligatorio antes de tocar e-CF:

- Validación de suma en pago mixto (`monto_efectivo + monto_transferencia + monto_tarjeta == total`). Si los pagos del e-CF no suman al total, MSeller (y luego DGII) los rechazará.
- `transaction.atomic` envolviendo `api_anular_venta` completo. Una anulación inconsistente que ya disparó Nota de Crédito Electrónica aprobada genera problema fiscal real.
- Mover `print_manager.print_ticket_venta` fuera de la transacción de venta (ya tiene patrón con `transaction.on_commit` para sync; aplicar igual).

Tiempo estimado: un fin de semana.

**0.2 Cuenta MSeller**

- Crear cuenta en https://ecf.mseller.app/ con el RNC de Royal Plast (cliente piloto)
- Subir el `.p12` al panel de MSeller, validar contraseña
- Generar API Key
- Verificar acceso a documentación de su API REST
- Confirmar que el tier gratuito (200 docs/mes) está activo y no requiere tarjeta de crédito para empezar

**0.3 Repositorio de la librería nativa**

- Crear repo privado `dgii-ecf-py` en GitHub (separado de `pos_fifo_system`)
- Reservar el nombre en PyPI con un placeholder `0.0.1` (chequear disponibilidad de `dgii-ecf` o variantes antes de comprometerse al nombre)
- Inicializar con: `LICENSE` (MIT, aunque privado el repo, declarar la licencia desde ya), `README.md` placeholder, `pyproject.toml` con stack mínimo (`signxml`, `lxml`, `cryptography`, `pytest`)
- Estructura inicial vacía pero presente: `dgii_ecf/cert/`, `dgii_ecf/xml/`, `dgii_ecf/signing/`, `tests/`

**0.4 Lectura de referencia**

- Clonar `victors1681/dgii-ecf` localmente para consulta
- Leer (no copiar) los módulos `auth/`, `signature/`, `transformer/`, `ecf/` para entender el flujo end-to-end
- Anotar diferencias entre lo que hace el código y lo que dice la documentación oficial DGII (a veces difieren — el código real es la fuente de verdad sobre qué espera DGII)

**0.5 Documentación oficial DGII**

- Descargar a `docs/ecf/dgii/`: manual técnico de e-CF, especificación de firma, XSDs por tipo (32 prioridad, 31 y 34 después)
- Versionar con fecha de descarga

**Entregables Semana 0:**
- Bugs críticos fixed y deployados a los 3 clientes actuales
- Cuenta MSeller activa con .p12 cargado y API key
- Repo `dgii-ecf-py` creado y vacío, con LICENSE y estructura
- Documentación DGII descargada y versionada

---

## Semana 1 — Abstracción e infraestructura

> *Diseñar bien la interfaz al principio cuesta medio día y ahorra semanas después.*

**1.1 Crear app `apps/facturacion_electronica/`**

```
apps/facturacion_electronica/
    __init__.py
    apps.py
    admin.py
    models.py
    interfaces.py        # EmisorECFInterface (contrato abstracto)
    services/
        __init__.py
    integrations/
        __init__.py
    migrations/
```

Agregar a `INSTALLED_APPS`. Migración inicial vacía.

**1.2 Definir `EmisorECFInterface`**

Interfaz abstracta usando `abc.ABC`. Métodos mínimos:

- `emitir(ecf_data: dict) -> ResultadoEmision` — sincrónico, retorna track_id + estado inicial
- `consultar_estado(track_id: str) -> EstadoECF` — para polling diferido
- `emitir_nota_credito(ecf_original: ECF, motivo: str) -> ResultadoEmision` — para anulaciones
- `descargar_xml_aprobado(track_id: str) -> bytes` — para almacenamiento local

`ResultadoEmision` y `EstadoECF` son dataclasses, no atadas a ningún proveedor.

**1.3 Modelos**

```python
class Emisor(models.Model):
    rnc, razon_social, nombre_comercial, direccion
    proveedor_actual  # 'mseller' | 'nativo'
    config_proveedor  # JSONField con datos específicos del proveedor activo
    activo

class ECF(models.Model):
    emisor (FK)
    venta (FK, nullable — tipo 34 puede no tener venta directa si es ajuste)
    tipo  # '31' | '32' | '34'
    encf  # E32xxxxxxxxxxx
    fecha_emision
    estado  # PENDIENTE, ENVIADO, EN_PROCESO, APROBADO, APROBADO_CONDICIONAL, RECHAZADO, ERROR
    track_id
    codigo_seguridad
    proveedor_usado  # 'mseller' | 'nativo' — para trazabilidad histórica
    xml_firmado  # TextField, lo guardamos aunque MSeller también lo guarde
    xml_respuesta  # TextField, respuesta cruda de DGII
    intentos
    creado_en, actualizado_en

class EventoECF(models.Model):
    ecf (FK)
    fecha
    estado_anterior, estado_nuevo
    mensaje
    payload  # JSONField
```

Admin Django para los tres modelos. Búsqueda y filtros básicos.

**1.4 Extensión de `ConfiguracionNegocio`**

Agregar al modelo singleton de `apps/configuracion`:

- `ecf_activo` (Boolean) — feature flag por cliente
- `ecf_proveedor` (Choice: 'mseller', 'nativo') — qué implementación usar
- Referencia a `Emisor` (FK opcional)

Con esto, un cliente sin `ecf_activo=True` sigue operando con NCF papel sin cambios.

**Entregables Semana 1:**
- App `facturacion_electronica` instalada y migrada
- `EmisorECFInterface` definida en `interfaces.py` con docstrings completos
- Modelos `Emisor`, `ECF`, `EventoECF` con admin
- Configuración por tenant funcional
- Tests unitarios mínimos: crear ECF, transición de estados, validación de feature flag

---

## Semana 2 — Implementación MSeller

> *Tu primer consumidor de la interfaz. Hace de validador de que el contrato está bien diseñado.*

**2.1 `services/mseller_emisor.py`**

Clase `MSellerEmisor(EmisorECFInterface)` con:

- Cliente HTTP usando `requests` (ya en stack)
- Manejo de API Key vía variable de entorno (no hardcoded)
- Métodos implementados: `emitir`, `consultar_estado`, `emitir_nota_credito`, `descargar_xml_aprobado`
- Mapeo de respuestas MSeller a `ResultadoEmision`/`EstadoECF` (DTOs neutros)
- Manejo de errores con reintentos con backoff exponencial para errores transitorios
- Logging detallado a `logs/ecf_mseller.log`

**2.2 Mapper Venta → JSON MSeller**

`services/venta_to_mseller.py` con función `venta_a_payload(venta, tipo)`. Maneja:

- Determinación de tipo (31 si `venta.cliente.rnc` válido y > 0 ITBIS, 32 caso contrario)
- Cálculo de ITBIS desglosado por línea
- Descuentos
- Redondeo (cuidado con decimales — DGII es estricto, MSeller también)
- Validación previa: si los datos no completan, retornar error claro antes de llamar API

**2.3 Persistencia de respuestas**

- Cada llamada a MSeller persiste el request, response, y track_id en `ECF` + `EventoECF`
- Descarga del XML aprobado se ejecuta en management command separado (no en hot path)
- Importante: aunque MSeller almacena 10 años, **descargamos copia local también** para protección ante cambio de PSFE futuro

**2.4 Tests**

- Mock de la API MSeller usando `responses` o `httpx-mock`
- Casos: emisión exitosa tipo 32, emisión con cliente RNC tipo 31, error transitorio con retry, error permanente sin retry, anulación tipo 34
- Tests de mapper: ventas con descuentos, ventas con varias líneas, redondeo

**Entregables Semana 2:**
- `MSellerEmisor` funcional contra el ambiente de pruebas de MSeller
- Mapper Venta→MSeller con tests
- Smoke test manual: emitir una venta de prueba contra MSeller test, recibir track_id, consultar estado, verificar que llega a APROBADO en MSeller dashboard
- Logs y trazabilidad funcionando

---

## Semana 3 — Integración con flujo de venta

> *El código de negocio nunca conoce a MSeller. Solo conoce la interfaz.*

**3.1 Hook en venta exitosa**

En `apps/ventas/services/ventas_service.py` (extraído como parte del fix de bugs en Semana 0, si no se extrajo antes), después del `transaction.on_commit` de sync, agregar:

```python
if config.ecf_activo:
    transaction.on_commit(
        lambda: emitir_ecf_async(venta)
    )
```

`emitir_ecf_async`:
- Obtiene el provider correcto según `config.ecf_proveedor`
- Construye el dict ECF
- Llama `provider.emitir(ecf_data)`
- Persiste resultado
- Registra `EventoECF`

Importante: este flujo es **async respecto a la venta**. La cajera no espera. Si MSeller tarda 5 segundos o se cae, la venta ya retornó éxito.

**3.2 Hook en anulación**

En `services/anulaciones_service.py`, después del `transaction.on_commit` de sync, agregar emisión de NC tipo 34 si `venta.ecf_set` tiene un ECF aprobado:

```python
ecf_original = venta.ecf_set.filter(estado='APROBADO').first()
if ecf_original:
    transaction.on_commit(
        lambda: emitir_nc_async(ecf_original, motivo)
    )
```

**3.3 Reintentos asincrónicos**

Management command `ecf_reintentar_pendientes`:
- Toma ECFs en estados `PENDIENTE`, `ENVIADO`, `EN_PROCESO` con `intentos < 5`
- Reenvía o consulta estado según corresponda
- Corre cada N minutos vía Task Scheduler de Windows (mismo patrón que `sincronizar` del sync engine)

**3.4 UI mínima**

- Indicador de estado de e-CF en `venta_exitosa` (Pendiente / Aprobado / Rechazado)
- Botón "Reimprimir ticket con QR" disponible una vez aprobado
- Vista admin de ECFs con filtros por estado, fecha, cliente

**3.5 Decisión: ticket impreso pre-aprobación**

Diseño recomendado: el ticket térmico se imprime al cierre de venta con leyenda "e-CF en proceso" y NCF en blanco. Una vez aprobado (segundos a minutos después), opción de reimprimir con eNCF + QR. Esto evita que la cajera espere por DGII en cada venta.

Alternativa: ticket sin NCF visible, e-CF se entrega solo por email al cliente. Menos fricción operativa pero requiere que cliente provea email. Decisión a tomar según política del cliente.

**Entregables Semana 3:**
- Hook async funcional en venta y anulación
- Reintentos automatizados
- UI mínima de estado e-CF
- Test end-to-end manual: venta en POS de pruebas → e-CF emitido a MSeller test → estado APROBADO visible en admin

---

## Semana 4 — Hardening y cliente piloto en producción

> *Estabilizar antes de escalar.*

**4.1 Robustez operativa**

- Manejo de pérdida de conexión a MSeller: cola local con reintentos automáticos
- Detección de credenciales expiradas / API key inválida: alerta clara, no falla silenciosa
- Validación previa: si la venta no tiene los campos mínimos para e-CF, alertar al cajero antes del cierre (no después)
- Manejo de timeout: si MSeller no responde en N segundos, ECF queda en `PENDIENTE` y se reintenta async

**4.2 Logging y métricas**

- Logs separados: `ecf_emisiones.log`, `ecf_errores.log`, `ecf_mseller.log`
- Dashboard simple en admin con: emitidos hoy, aprobados, pendientes, rechazados, tasa de aprobación últimos 7 días
- Alertas (email al SYSADMIN) ante: rechazo de DGII, cola de pendientes > 50, tasa de error > 5%

**4.3 Documentación operativa**

Crear `docs/ecf/OPERACIONES.md`:
- Cómo verificar estado de un ECF específico
- Cómo forzar reintento manual
- Qué hacer si MSeller cambia API
- Cómo solicitar nuevos rangos de eNCF en MSeller
- Contactos de soporte MSeller y DGII

**4.4 Onboarding del cliente piloto**

- Activar `ecf_activo=True` y `ecf_proveedor='mseller'` en `ConfiguracionNegocio` de Royal Plast (o cliente piloto que se elija)
- Período de paralelismo: una semana donde se emiten e-CF reales pero también se mantiene NCF papel como respaldo
- Verificación cruzada: cada venta del día tiene un ECF aprobado en MSeller
- Capacitación al cajero sobre los nuevos estados y reimpresión

**4.5 Decisión de cliente piloto**

Recomendación: empezar por el cliente con menor volumen (probablemente Royal Plast o el que tenga menos ventas/día). Razón: si algo falla en los primeros días, el impacto operativo es menor. Auto repuestos puede esperar 2-3 semanas si tiene volumen mayor.

**Entregables Semana 4:**
- Cliente piloto facturando con MSeller en producción
- Cero rechazos no entendidos en los primeros 3 días
- Logs y alertas operativas funcionando
- Documentación operativa escrita
- Decisión informada: ¿se mantiene el cliente piloto solo otra semana antes de migrar al segundo, o se va con confianza?

---

## Semana 5-6 — Kickoff librería nativa (proyecto paralelo)

> *El POS sigue funcionando con MSeller. Esta es construcción que no toca producción.*

**Mentalidad de trabajo:**

Cada commit a `dgii-ecf-py` se escribe como si fuera a ser leído por un dev externo en el futuro. Eso significa: docstrings completos, tipo hints en todas las funciones públicas, README que explique el propósito, ejemplos en `examples/`, no copy-paste mecánico desde el código Node.

**5.1 `dgii_ecf.cert.P12Reader`**

Pieza autocontenida. Responsabilidad: cargar `.p12`, validar contraseña, retornar `(private_key, certificate, chain)` usando `cryptography`. Validación de expiración con warning si quedan menos de 30 días.

API pública:
```python
from dgii_ecf.cert import P12Reader

reader = P12Reader.from_file("path/to/cert.p12", password="...")
key = reader.private_key
cert = reader.certificate
expires_at = reader.expires_at  # datetime
```

Tests con `.p12` de prueba (NO el cert real de cliente — usar uno generado para tests). Suite de tests cubre: contraseña válida, contraseña inválida, archivo no encontrado, cert expirado, cert por expirar.

Referencia Node: módulo `P12Reader` en victors1681/dgii-ecf. Léelo para entender qué metadata extrae, después escribí el equivalente Python con `cryptography.hazmat.primitives.serialization.pkcs12`.

**5.2 `dgii_ecf.xml.builders.Builder32`**

Construcción del XML conforme al XSD oficial DGII tipo 32. Builder pattern con dataclass de entrada:

```python
from dgii_ecf.xml.builders import Builder32, ECFInput, Emisor, Comprador, Item

xml_str = Builder32().build(
    ECFInput(
        emisor=Emisor(rnc="...", razon_social="..."),
        comprador=Comprador(...),  # puede ser None para tipo 32
        items=[Item(...)],
        encf="E320000000001",
        fecha=date.today(),
        ...
    )
)
```

El builder usa `lxml` para construcción + namespace handling. Salida es bytes UTF-8 sin firmar.

**5.3 `dgii_ecf.xml.validators.XSDValidator`**

Validación contra XSD oficial. Usa `lxml.etree.XMLSchema`. Los XSD se incluyen en el paquete (en `dgii_ecf/schemas/`) versionados por fecha de descarga DGII.

```python
from dgii_ecf.xml.validators import XSDValidator

validator = XSDValidator.for_type("32")
result = validator.validate(xml_bytes)
if not result.valid:
    print(result.errors)  # lista de errores con línea/columna
```

**5.4 `dgii_ecf.signing.XAdESSigner` (preliminar)**

Wrapper sobre `signxml.xades.XAdESSigner` con configuración específica DGII (algoritmo SHA-256, perfil EPES, política de firma, ubicación correcta de la firma en el XML).

```python
from dgii_ecf.signing import XAdESSigner
from dgii_ecf.cert import P12Reader

reader = P12Reader.from_file("cert.p12", password="...")
signer = XAdESSigner(private_key=reader.private_key, certificate=reader.certificate)
signed_xml = signer.sign(unsigned_xml)
```

**Esta es la pieza más difícil técnicamente.** El perfil exacto que DGII espera (qué referencias, qué transformaciones, qué política de firma) hay que verificarlo con la documentación oficial + comparar con lo que produce victors1681/dgii-ecf para asegurar compatibilidad. Reservar tiempo extra acá.

**5.5 Cross-validation con Node.js**

Para cada pieza Python que escribís, generar el mismo input contra el código Node y comparar outputs:

- Mismo `.p12`, misma contraseña → ¿extraen los mismos metadatos?
- Mismo input ECF → ¿generan XMLs equivalentes? (puede haber diferencias menores en formatting/whitespace; lo crítico es contenido y estructura)
- Mismo XML sin firmar + mismo cert → ¿la firma resultante valida con el mismo verificador?

Esto NO es para copiar el output de Node a Python. Es para verificar que tu interpretación del estándar coincide con la del código de referencia. Si difieren, investigar cuál está bien (la doc oficial es el árbitro).

**5.6 Testing infrastructure**

- `pytest` con coverage
- Fixtures: `.p12` de prueba generado para CI, XML samples válidos e inválidos
- GitHub Actions para correr tests en cada push (incluso en repo privado)

**Entregables Semana 5-6:**
- `dgii-ecf-py` con `P12Reader`, `Builder32`, `XSDValidator`, `XAdESSigner` funcionando
- Tests verdes con > 80% coverage
- README inicial con descripción del propósito y estado actual ("Pre-alpha, en desarrollo")
- CHANGELOG con primera entrada
- Documentado: qué piezas faltan para emisión end-to-end (DGIIClient con auth por semilla, manejo de respuestas, builders 31 y 34)

---

## Riesgos durante esta fase

**R1: Bugs no descubiertos en `apps/ventas` salen a flote al integrar e-CF.**
Mitigación: Semana 0 con fix obligatorio. No empezar e-CF si quedan bugs críticos.

**R2: MSeller cambia API o pricing durante la integración.**
Mitigación: contrato/términos verificados antes de empezar. Almacenamiento local de XMLs aprobados desde el día 1.

**R3: La interfaz `EmisorECFInterface` queda mal diseñada y hay que cambiarla cuando llegue la implementación nativa.**
Mitigación: revisión de diseño explícita al final de Semana 1, antes de implementar MSeller. Si se descubre limitación durante MSeller, cambiar interfaz tempranamente, no postergar.

**R4: La firma XAdES Python no produce output compatible con DGII.**
Mitigación: Semana 5-6 reserva buffer para esto. Cross-validation con Node es la red de seguridad. Si no se logra paridad en 1 semana, marcarlo como bloqueante y dedicar Semana 7 a resolver antes de seguir con builders 31/34.

**R5: El proyecto paralelo se queda en 70% por falta de presión.**
Mitigación: hito comprometido para fin de Fase 2 (Semana 12 calendario): "primer e-CF tipo 32 emitido contra TesteCF de DGII desde código Python nativo, sin pasar por MSeller". Hito binario, verificable, no postergable a "cuando tenga tiempo".

---

## Decisiones que tomar antes de empezar

1. **Cliente piloto.** ¿Royal Plast o auto repuestos? Recomendación: el de menor volumen.
2. **Política de impresión pre-aprobación.** ¿Ticket con "e-CF en proceso" + reimpresión cuando aprobe? ¿O ticket sin NCF + email al cliente con el ECF aprobado?
3. **Nombre del paquete Python.** Verificar disponibilidad en PyPI: `dgii-ecf`, `dgii-ecf-py`, `pyecf-do`, `python-ecf-dgii`. Reservar el elegido con `0.0.1` placeholder.
4. **Política de credenciales.** ¿Variable de entorno por cliente? ¿Archivo `.env` cifrado? ¿KMS local? Para deployment on-premise actual, env var con permisos restrictivos al servicio NSSM es razonable.

---

## HANDOFF — Estado al cerrar Fase Inicial

Cuando se cumplan los entregables de Semanas 0-6, el estado del sistema será:

### Lo que tenés

- Cliente piloto facturando e-CF en producción vía MSeller, con tasa de aprobación >95% y operación estable por al menos 2 semanas
- Otros 2 clientes pendientes de migración (con `ecf_activo=False` por ahora)
- App `apps/facturacion_electronica` con interfaz limpia, MSeller como única implementación, modelos de tracking persistiendo histórico completo
- Bugs críticos del módulo ventas resueltos
- Repo `dgii-ecf-py` privado con piezas de bajo nivel (`P12Reader`, `Builder32`, `XSDValidator`, `XAdESSigner`) funcionando con tests
- Documentación operativa para gestión diaria de e-CF
- Logs, métricas y alertas funcionales

### Lo que NO tenés todavía

- Migración de los otros 2 clientes a e-CF (queda como Fase 2.0)
- Builders para tipos 31 y 34 en la librería nativa
- DGIIClient en la librería nativa (autenticación por semilla, envío, consulta de estado)
- Manejo de ARECF (acuse de recibo) para tipo 31
- Modo contingencia automático (si DGII se cae)
- Set de pruebas pasado en TesteCF
- Certificación oficial DGII para emisión nativa
- Representación impresa con QR (actualmente lo provee MSeller)

### Decisiones tomadas durante Fase Inicial que afectan Fase 2

Espacio reservado para que se documenten al cerrar la fase:

- [ ] Nombre definitivo del paquete Python: ___________
- [ ] Cliente piloto elegido: ___________
- [ ] Política de impresión pre-aprobación: ___________
- [ ] Estructura de la interfaz `EmisorECFInterface` validada o ajustada: ___________
- [ ] Tasa de aprobación observada en MSeller: ___________
- [ ] Tiempo promedio de aprobación observado: ___________

### Aprendizajes para registrar

Espacio para que se documenten al cerrar la fase. Tipo de cosas a anotar:

- Edge cases descubiertos en el mapper Venta → ECF
- Comportamientos no documentados de MSeller
- Diferencias entre la documentación DGII y lo que el código Node implementa
- Decisiones de diseño en `dgii-ecf-py` que conviene revisitar

### Próxima fase (Fase 2 — borrador, refinar al cerrar Inicial)

**Objetivo de Fase 2:** llevar `dgii-ecf-py` a paridad funcional con MSeller para tipo 32, pasar set de pruebas DGII en TesteCF, y migrar el cliente piloto a emisión nativa.

Bloques de trabajo identificados:

- **Bloque A — Completar la librería nativa.** Builders 31 y 34, `DGIIClient` con autenticación por semilla, envío de documentos, consulta de estado, manejo de respuestas DGII. Reintentos con backoff. Modo contingencia básico. Esto es el grueso del trabajo (~6-8 semanas).
- **Bloque B — Set de pruebas.** Estudiar el set oficial, implementar cada caso, iterar contra TesteCF hasta tener 100% verde. Esta fase NO es lineal — depende de respuestas de DGII (~4-6 semanas, alta varianza).
- **Bloque C — Implementación `NativoEmisor`.** En `apps/facturacion_electronica/services/nativo_emisor.py`, implementar `EmisorECFInterface` consumiendo `dgii-ecf-py`. Tests de integración. Persistencia de XMLs y respuestas (~1-2 semanas).
- **Bloque D — Migración del piloto.** Feature flag por cliente. Cambio de `ecf_proveedor` de 'mseller' a 'nativo' para Royal Plast. Período de paralelismo de 2 semanas. Validación de que la tasa de aprobación con nativa iguala o supera la de MSeller (~3-4 semanas calendario incluyendo paralelismo).
- **Bloque E — Decisión sobre publicación OSS.** Cuando `dgii-ecf-py` haya pasado set de pruebas y esté en producción con un cliente, evaluar: ¿publicar en GitHub público + PyPI? ¿Cuándo? ¿Con qué nivel de soporte comprometido? Esta decisión depende del aprendizaje de Fase Inicial sobre interés y tiempo disponible.

Estimación gruesa Fase 2: 4-6 meses calendario para un dev part-time. Más buffer que Fase Inicial porque la dependencia de DGII (set de pruebas, certificación) introduce varianza no controlable.

**No hacer Fase 2 sin antes:**
- Confirmar que MSeller en producción está estable (>1 mes sin incidencias mayores)
- Confirmar que las piezas de `dgii-ecf-py` en Fase Inicial pasaron sus tests con buena cobertura
- Tener tiempo dedicado consistente — Fase 2 es donde el "proyecto paralelo" puede atascarse si se le da migajas

### Para arrancar Fase 2 con buen pie

Cuando llegue el momento, las primeras tareas son:

1. Releer este HANDOFF y completar las secciones marcadas con [ ] o "_______"
2. Actualizar la documentación oficial DGII (versión más reciente de XSDs, manuales)
3. Estudiar el set de pruebas oficial completo antes de empezar a implementar
4. Revisar el código de `victors1681/dgii-ecf` para los flujos que faltan (auth por semilla específicamente)
5. Definir cronograma con hitos verificables, no fechas vagas

