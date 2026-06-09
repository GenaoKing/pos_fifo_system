# TESTING e-CF — Bitácora y Plan de Ejecución

> Estado documental: bitacora historica de ejecucion e-CF. Conservar por
> evidencia y troubleshooting, no usar como roadmap vivo. Ver
> `PROJECT_STATUS.md` y `ROADMAP_ECF_FASE_INICIAL.md`.

**Fecha:** 9-10 de mayo de 2026  
**Objetivo:** validar el módulo `facturacion_electronica` por capas antes de seguir implementando a ciegas.  
**Estado de esta bitácora:** fase de testing development ejecutada y documentada.

---

## 1. Preflight

### 1.1 Hallazgos iniciales del repo

- `config/urls.py` ya incluye `path('facturacion-electronica/', include('apps.facturacion_electronica.urls'))`.
- `config/settings.py` ya tiene `LOGGING` para `ecf.*` y `ventas.service`.
- `templates/base.html` ya carga `static/js/ecf_estado.js`.
- `templates/pos/venta_exitosa.html` ya usa `ecfEstadoBadge(...)`.
- `deploy/activar_ecf_dev.bat` está listo para testing local y tiene credenciales de TesteCF cargadas en texto plano.

### 1.2 Nota operativa importante

- Esta sesión de Codex no puede ejecutar `manage.py` aquí porque el entorno actual no tiene Django importable.
- Todas las pruebas de shell se deben correr desde tu shell local ya activado con `deploy\activar_ecf_dev.bat ...`.
- No pegar ni versionar secretos en esta bitácora.

### 1.3 Preflight checklist manual

- [x] Correr migraciones desde tu shell activado.
- [x] Verificar que el `Emisor` esté activo y asociado en `ConfiguracionNegocio`.
- [x] Confirmar que `modulo_ecf=True`.
- [x] Confirmar que el entorno del emisor sea `TesteCF`.
- [x] Verificar que exista al menos una `Venta` real `COMPLETADA` para pruebas aisladas.
- [ ] Verificar si hay impresora térmica conectada para la Fase E.

---

## 0. Cierre ejecutivo

### 0.1 Resultado general

- El flujo e-CF quedó validado en development para tipo `32` desde varias capas:
  - mapper
  - builder payload
  - cola persistente
  - cliente HTTP MSeller
  - procesador end-to-end
  - integración UI de `venta_exitosa`
- Se confirmó integración real con TesteCF y llegada a estado terminal `APROBADO`.
- Se corrigió una ambigüedad importante del diseño: la emisión normal ya no usa `validate=true`.

### 0.2 Qué quedó probado

- Emisión real y consulta de estado en TesteCF
- Persistencia de `ECF`, `EventoECF`, `encf`, `track_id`, `codigo_seguridad`, `xml_firmado`, `xml_respuesta`
- Flujo shell controlado y flujo real vía POS
- Badge de estado con `eNCF` correcto en `venta_exitosa`

### 0.3 Qué queda pendiente

- Validación manual de impresión térmica con impresora real
- Si se desea, prueba específica de tipo `31`
- Tests unitarios/integración automatizados
- Preparación operativa de producción: Task Scheduler/onboarding

---

## 2. Convención para registrar resultados

Usar este formato corto por cada prueba ejecutada:

```text
Fecha/hora:
Fase:
Caso:
Venta:
ECF ID:
eNCF:
Resultado esperado:
Resultado real:
Hallazgos:
Acción siguiente:
```

---

## 3. Fase B — Tests aislados sin tocar MSeller

Objetivo: validar mapper, builder y cola persistente sin depender de red ni de DGII.

### 3.1 Preparación en `manage.py shell`

```python
from pprint import pprint
from decimal import Decimal

from apps.ventas.models import Venta
from apps.configuracion.utils import get_config
from apps.facturacion_electronica.models import ECF
from apps.facturacion_electronica.services.venta_to_ecf import venta_a_ecf_data
from apps.facturacion_electronica.integrations.mseller_payload import build_mseller_payload
from apps.facturacion_electronica.services.cola_emision import encolar_emision

config = get_config()
emisor = config.emisor_activo

venta = (
    Venta.objects
    .filter(estado='COMPLETADA')
    .exclude(id__in=ECF.objects.exclude(venta_id=None).values_list('venta_id', flat=True))
    .order_by('-fecha_venta')
    .first()
)

print('Venta elegida:', venta.id if venta else None, venta.numero_venta if venta else None)
print('Cliente:', getattr(venta.cliente, 'nombre', None) if venta and venta.cliente else 'CONTADO/None')
```

Si `venta` sale `None`, elegir una `Venta` completada manualmente y aceptar que la prueba creará un `ECF` asociado.

### 3.2 Caso B1 — Mapper `venta_a_ecf_data()`

```python
ecf_data = venta_a_ecf_data(venta, tipo_ecf='32')
pprint(ecf_data)

print('Tipo:', ecf_data['tipo'])
print('Comprador:', ecf_data['comprador'])
print('Cantidad items:', len(ecf_data['items']))
print('Totales:', ecf_data['totales'])

suma_items = sum((item['monto_item'] for item in ecf_data['items']), Decimal('0'))
suma_itbis = ecf_data['totales']['total_itbis']
monto_total = ecf_data['totales']['monto_total']

print('Suma items base:', suma_items)
print('Suma items + ITBIS:', suma_items + suma_itbis)
print('Monto total dict:', monto_total)
```

Validar:

- `tipo == '32'`
- `items` no vacío
- `monto_total == suma de bases + total_itbis`
- `comprador is None` si la venta es de contado genérico
- `indicador_facturacion` por línea consistente con la configuración fiscal
- `monto_gravado_18`, `monto_gravado_16`, `monto_exento` coherentes con los productos

### 3.3 Caso B2 — Builder `build_mseller_payload()`

```python
ecf_data['emisor'] = {
    'rnc': emisor.rnc,
    'razon_social': emisor.razon_social,
    'nombre_comercial': emisor.nombre_comercial or '',
    'direccion': emisor.direccion or '',
}

payload = build_mseller_payload(ecf_data, encf='E320000000001')
pprint(payload)

encabezado = payload['ECF']['Encabezado']
print('IdDoc:', encabezado['IdDoc'])
print('Emisor:', encabezado['Emisor'])
print('Totales:', encabezado['Totales'])
print('Cantidad items payload:', len(payload['ECF']['DetallesItems']['Item']))
```

Validar:

- existe `payload['ECF']['Encabezado']`
- `IdDoc.TipoeCF == '32'`
- `IdDoc.eNCF == 'E320000000001'`
- `Emisor.RNCEmisor` y `RazonSocialEmisor` correctos
- `Totales.MontoTotal` coincide con el mapper
- no se envían campos innecesarios en cero si no aplican
- `Comprador` solo aparece si hay comprador fiscal

### 3.4 Caso B3 — Cola de emisión

```python
ecf = encolar_emision(venta=venta, tipo_ecf='32')
print('ECF creado:', ecf.id if ecf else None)

ecf.refresh_from_db()
print('Estado:', ecf.estado)
print('Tipo:', ecf.tipo)
print('Intentos:', ecf.intentos)
print('eNCF:', ecf.encf)

evento = ecf.eventos.order_by('fecha').first()
print('Evento inicial:', evento.estado_anterior, '->', evento.estado_nuevo)
print('Mensaje evento:', evento.mensaje)
print('Payload evento:', evento.payload)
```

Validar:

- se crea `ECF`
- `estado == PENDIENTE`
- `encf == ''`
- `intentos == 0`
- existe un `EventoECF` inicial con origen `encolar_emision`

### 3.5 Registro de resultados — Fase B

- B1 Mapper:
  Resultado real: `venta_id=23`, `numero_venta=VENTA-20260423-00001`, cliente `CONTADO/None`. El mapper produjo `tipo='32'`, `comprador=None`, `2` items, `monto_gravado_18=29.66`, `total_itbis=5.34`, `monto_total=35.00`.
  Hallazgos: la suma de bases (`29.66`) + ITBIS (`5.34`) cuadra exactamente con `monto_total=35.00`. El caso de cliente contado genérico se comportó como se esperaba: no se incluyó comprador fiscal.

- B2 Payload:
  Resultado real: el payload generado para `encf='E320000000001'` incluyó `Encabezado`, `DetallesItems`, `IdDoc.TipoeCF='32'`, `IdDoc.eNCF='E320000000001'`, `RNCEmisor='131822096'`, `RazonSocialEmisor='Tabacalera Genao SRL'`, `MontoTotal=35.0` y `2` líneas.
  Hallazgos: la estructura base enviada a MSeller luce coherente para tipo `32`; no apareció bloque `Comprador`, lo cual es correcto para esta venta de contado. Tampoco se enviaron campos de 16% o exento en cero cuando no aplicaban.

- B3 Cola:
  Resultado real: `encolar_emision(venta=23, tipo_ecf='32')` creó `ECF id=1` en `PENDIENTE`, `intentos=0`, `encf=''`, con evento inicial `'' -> PENDIENTE`.
  Hallazgos: el `EventoECF` inicial quedó bien trazado con `payload={'origen': 'encolar_emision', 'tipo_ecf': '32', 'venta_id': 23, 'venta_numero': 'VENTA-20260423-00001'}`. La capa de cola quedó validada sin tocar MSeller.

---

## 4. Fase C — Test contra MSeller (HTTP real a TesteCF)

Objetivo: validar cliente HTTP y contrato con TesteCF sin pasar todavía por toda la cola.

### 4.1 Preparación

```python
from pprint import pprint
from datetime import datetime

from apps.configuracion.utils import get_config
from apps.facturacion_electronica.services.mseller_http_client import (
    MSellerConfig,
    MSellerHTTPClient,
)
from apps.facturacion_electronica.services.venta_to_ecf import venta_a_ecf_data
from apps.facturacion_electronica.integrations.mseller_payload import build_mseller_payload

config = get_config()
emisor = config.emisor_activo
client = MSellerHTTPClient(MSellerConfig.from_emisor_config(emisor.config_proveedor))

venta = venta  # reutilizar la seleccionada en Fase B, o volver a definirla
ecf_data = venta_a_ecf_data(venta, tipo_ecf='32')
ecf_data['emisor'] = {
    'rnc': emisor.rnc,
    'razon_social': emisor.razon_social,
    'nombre_comercial': emisor.nombre_comercial or '',
    'direccion': emisor.direccion or '',
}

suffix_validate = datetime.now().strftime('%d%H%M%S%f')[:10]
suffix_emit = datetime.now().strftime('%m%d%H%M%S%f')[:10]
encf_validate = f'E32{suffix_validate}'
encf_emit = f'E32{suffix_emit}'

payload_validate = build_mseller_payload(ecf_data, encf=encf_validate)
payload_emit = build_mseller_payload(ecf_data, encf=encf_emit)
```

### 4.2 Caso C1 — Auth

```python
token = client._authenticate()
print('Auth OK:', bool(token), 'len=', len(token))
```

Esperado:

- autentica sin excepción
- retorna `idToken`

### 4.3 Caso C2 — Validación previa `validate=true`

```python
resp_validate = client.enviar_documento(payload_validate, validar=True)
pprint(resp_validate)
```

Esperado:

- respuesta tipo `{valid: true, ...}` o mensaje equivalente de validación satisfactoria
- no debería consumir secuencia operativa

### 4.4 Caso C3 — Emisión real `validate=false`

```python
resp_emit = client.enviar_documento(payload_emit, validar=False)
pprint(resp_emit)

print('eNCF:', resp_emit.get('ecf'))
print('track:', resp_emit.get('internalTrackId'))
print('securityCode:', resp_emit.get('securityCode'))
print('qr_url:', resp_emit.get('qr_url'))
```

Esperado:

- respuesta exitosa
- trae `ecf`, `internalTrackId`, `securityCode`, `qr_url`

### 4.5 Caso C4 — Consulta de estado

```python
encf_emitido = resp_emit.get('ecf')
estado = client.consultar_documento(encf_emitido)
pprint(estado)
```

Esperado:

- retorna documento por `ecf`
- `status` en alguno de: `RECIBIDO`, `PROCESANDO`, `Aceptado`, `Rechazado`

### 4.6 Registro de resultados — Fase C

- C1 Auth:
  Resultado real: autenticación exitosa contra `https://ecf.api.mseller.app/TesteCF/customer/authentication`; `idToken` recibido con longitud `1502`.
  Hallazgos: al invocar directamente el método privado `client._authenticate()`, la siguiente llamada a `enviar_documento()` volvió a autenticar. Esto sugiere que `_authenticate()` retorna el token pero no lo deja cacheado en `self._id_token`; la caché normal se llena vía `_ensure_token()`. No es un bug del flujo productivo, pero sí un detalle a recordar en pruebas manuales.

- C2 Validate:
  Resultado real: `client.enviar_documento(payload_validate, validar=True)` devolvió una respuesta completa de documento, no un `{valid: true}` simple. Respuesta observada: `ecf='E320923093550'`, `internalTrackId='daba73cf-9e95-4179-bb33-5319708e9680'`, `securityCode='d5KLFi'`, `qr_url=...`, `signedDate='09-05-2026 11:10:28'`.
  Hallazgos: en TesteCF, `validate=true` no se comportó como "dry-run sin efectos" según la expectativa documentada. Como mínimo retorna artefactos de documento completos; por prudencia, no conviene asumir que es inocuo ni que "no consume secuencia" hasta confirmarlo mejor con MSeller.

- C3 Emitir:
  Resultado real: `client.enviar_documento(payload_emit, validar=False)` emitió exitosamente `ecf='E320509230935'`, `track='2450b935-0297-40fb-9ba0-10136170fe69'`, `securityCode='PPjXlZ'`, `qr_url=...`, `signedDate='09-05-2026 11:14:26'`.
  Hallazgos: quedó confirmado que la respuesta inmediata de emisión trae datos suficientes para ticket/reimpresión temprana: `eNCF`, `internalTrackId`, `securityCode` y `qr_url`.

- C4 Consultar:
  Resultado real: la consulta inmediata por `ecf='E320509230935'` retornó `status='Aceptado Condicional'`, `ncf='E320509230935'`, `securityCode='PPjXlZ'`, `internalTrackId='2450b935-0297-40fb-9ba0-10136170fe69'` y `dgiiResponse` con mensaje `Número de secuencia no autorizada.`
  Hallazgos: TesteCF aceptó el documento en estado terminal `Aceptado Condicional`, lo que calza con la decisión actual de usar secuencias locales en sandbox. También se observó que `dgiiResponse` llega como lista de strings JSON embebidos, no como objetos parseados; si luego queremos explotar esos mensajes en UI o reporting, habrá que parsearlos explícitamente.

---

## 5. Fase D — Test del procesador end-to-end

Objetivo: validar que la cola + procesador + persistencia de estado funcionen completas.

### 5.1 Caso D1 — Encolar y procesar emisión

```python
import time

from apps.facturacion_electronica.services.cola_emision import encolar_emision
from apps.facturacion_electronica.services.procesador import procesar_ecf

ecf = encolar_emision(venta=venta, tipo_ecf='32')
print('ECF encolado:', ecf.id, ecf.estado)

ecf.refresh_from_db()
resultado_1 = procesar_ecf(ecf)
print(resultado_1)

ecf.refresh_from_db()
print('Estado luego de emitir:', ecf.estado)
print('eNCF:', ecf.encf)
print('track_id:', ecf.track_id)
print('codigo_seguridad:', ecf.codigo_seguridad)
print('xml_firmado lleno:', bool(ecf.xml_firmado))
print('xml_respuesta llena:', bool(ecf.xml_respuesta))
```

Esperado:

- transición desde `PENDIENTE`
- pasa a `ENVIADO`, `EN_PROCESO` o excepcionalmente `RECHAZADO`/`ERROR`
- persiste `encf`
- persiste `track_id`
- persiste `xml_firmado` con JSON enviado
- persiste `xml_respuesta` con respuesta MSeller

### 5.2 Caso D2 — Espera y consulta

```python
time.sleep(30)

ecf.refresh_from_db()
resultado_2 = procesar_ecf(ecf)
print(resultado_2)

ecf.refresh_from_db()
print('Estado final:', ecf.estado)
print('codigo_seguridad:', ecf.codigo_seguridad)

for e in ecf.eventos.order_by('fecha'):
    print(e.fecha, e.estado_anterior, '->', e.estado_nuevo, '|', e.mensaje)
```

Esperado:

- si DGII ya respondió: `APROBADO` o `APROBADO_CONDICIONAL`
- si aún no: puede quedar en `EN_PROCESO`
- debe existir trazabilidad en `EventoECF`

### 5.3 Registro de resultados — Fase D

- D1 Emisión por procesador:
  Resultado real: con `venta_id=22`, `numero_venta=VENTA-20260422-00001`, `encolar_emision()` creó `ECF id=2` en `PENDIENTE`. La primera ejecución de `procesar_ecf(ecf)` produjo `✓ ECF#2 PENDIENTE → ENVIADO: Documento enviado a MSeller, pendiente DGII.`
  Hallazgos: tras la primera pasada quedaron persistidos `estado=ENVIADO`, `encf='E320000000001'`, `track_id='902ff318-3395-4772-bfdb-f9eb6f15de0b'`, `codigo_seguridad='f6bMmu'`, `xml_firmado=True` y `xml_respuesta=True`. La orquestación y persistencia del procesador quedaron validadas.

- D2 Consulta por procesador:
  Resultado real: después de `time.sleep(30)`, la segunda ejecución de `procesar_ecf(ecf)` produjo `✓ ECF#2 ENVIADO → APROBADO: Consulta OK: Aceptado`. El `ECF` quedó finalmente en `APROBADO`.
  Hallazgos: se verificó transición terminal completa `PENDIENTE -> ENVIADO -> APROBADO` con eventos persistidos en orden. `encf`, `track_id` y `codigo_seguridad` se mantuvieron estables entre emisión y consulta.

---

## 6. Fase E — Flujo completo desde POS

Objetivo: validar el recorrido real de usuario.

### 6.1 Caso E1 — Venta real desde POS

Checklist:

- [ ] Abrir POS de pruebas
- [ ] Crear venta simple con `tipo_ecf='32'`
- [ ] Guardar `venta_id` y `numero_venta`
- [ ] Confirmar que el frontend no rompió el cierre de venta

### 6.2 Caso E2 — Verificar hook de encolado

En shell:

```python
from apps.ventas.models import Venta
from apps.facturacion_electronica.models import ECF

venta = Venta.objects.get(id=VENTA_ID_AQUI)
ecfs = ECF.objects.filter(venta=venta).order_by('-creado_en')

print('Venta:', venta.numero_venta)
print('Cantidad ECFs:', ecfs.count())
for ecf in ecfs:
    print(ecf.id, ecf.tipo, ecf.estado, ecf.encf, ecf.creado_en)
```

Esperado:

- existe `ECF` asociado a la venta recién creada
- estado inicial `PENDIENTE`

### 6.3 Caso E3 — Correr management command manualmente

Sugerencia desde shell activado:

```bat
deploy\activar_ecf_dev.bat ecf_procesar_pendientes --solo-emitir --limite 5
deploy\activar_ecf_dev.bat ecf_procesar_pendientes --ecf-id ID_DEL_ECF
```

Esperado:

- el command procesa el `ECF`
- se ve transición de estado y logging útil

### 6.4 Caso E4 — Badge en `venta_exitosa`

Checklist:

- [ ] abrir la pantalla de venta exitosa
- [ ] verificar que el badge aparezca
- [ ] verificar polling automático
- [ ] verificar transición visual de `Pendiente/Enviado` a `Aprobado` o equivalente
- [ ] verificar si aparece botón/acción de reimpresión final

### 6.5 Caso E5 — Ticket térmico

Checklist:

- [ ] ticket inicial imprime sin romper cierre
- [ ] si el ECF ya tiene datos, imprime bloque fiscal
- [ ] si está pendiente, muestra leyenda de Envío Diferido o reimpresión
- [ ] si luego se reimprime aprobado, aparecen `eNCF`, código, QR

### 6.6 Registro de resultados — Fase E

- E1 POS:
  Resultado real: venta cerrada desde el POS sin ruptura del flujo.
  Hallazgos: el circuito real de usuario logró completar la venta y llegar a la pantalla esperada.

- E2 Hook:
  Resultado real: el flujo continuó hasta resolución final del e-CF.
  Hallazgos: no se reportó ruptura del hook de encolado; el resultado final aprobado sugiere que el encadenamiento POS -> venta -> e-CF funcionó.

- E3 Command:
  Resultado real: el documento terminó en estado final `APROBADO`.
  Hallazgos: la resolución final del e-CF quedó consistente con las fases previas de shell.

- E4 Badge:
  Resultado real: el polling funciona y el badge mostró el `eNCF` correcto. Al entrar a `venta_exitosa`, el badge ya se encontraba actualizado con el estado final aprobado.
  Hallazgos: quedó validado el render correcto del estado final `APROBADO` y la consulta del endpoint de estado. En esta corrida, no necesariamente se observó una transición visual "en vivo" desde `PENDIENTE/ENVIADO` porque la vista ya cargó con el documento resuelto.

- E5 Térmica:
  Resultado real:
  Hallazgos:

---

## 7. Hallazgos consolidados

### 7.1 Hallazgos confirmados antes de ejecutar pruebas

- `docs/handoffs/HANDOFF_ECF.md` quedó desactualizado en dos puntos respecto al repo actual:
  - todavía marca `config/urls.py` como pendiente, pero ya está incluido
  - todavía marca `LOGGING` e-CF como pendiente, pero ya existe en `config/settings.py`
- El flujo UI de polling ya está cableado en código.
- `deploy/activar_ecf_dev.bat` funciona como wrapper útil de testing, pero por seguridad conviene tratarlo como archivo local/no publicable si contiene credenciales reales.

### 7.2 Hallazgos durante ejecución

- Prueba adicional tipo `31` con cliente y RNC:
  - el documento llegó a consulta con `status='Error'`
  - respuesta DGII reportada por MSeller: `The element 'Encabezado' has invalid child element 'Comprador'. List of possible elements expected: 'OtraMoneda'.`
  - causa identificada en código: el builder estaba serializando `Comprador` después de `Totales`
  - fix aplicado: `build_mseller_payload()` ahora arma `Encabezado` en orden `Version -> IdDoc -> Emisor -> Comprador -> Totales`
- Segundo hallazgo de tipo `31` tras el primer fix:
  - nuevo documento `E310000000002` fue emitido, pero DGII respondió `The element 'IdDoc' has invalid child element 'FechaVencimientoSecuencia'...`
  - causa identificada en código: `_build_id_doc()` construía el dict base y luego agregaba `FechaVencimientoSecuencia`, por lo que el orden real dentro de `IdDoc` quedaba incorrecto
  - fix aplicado: `_build_id_doc()` ahora construye el payload de tipo `31` completo en el orden esperado por MSeller/DGII
- Tercer hallazgo de tipo `31` tras el segundo fix:
  - DGII respondió `The element 'Comprador' has invalid child element 'RazonSocialComprador'. List of possible elements expected: 'RNCComprador'.`
  - causa identificada en código: `_build_comprador()` serializaba primero `RazonSocialComprador` y luego `RNCComprador`
  - fix aplicado: `_build_comprador()` ahora arma el orden `RNCComprador -> RazonSocialComprador -> DireccionComprador`
- Hallazgo operativo al reintentar el mismo `ECF` tipo `31`:
  - el registro seguía mostrando `encf=E310000000001` aunque ya existía un segundo intento `E310000000002`
  - causa identificada en código: `_aplicar_resultado_emision()` solo persistía `resultado.encf` y `securityCode` si el campo en el modelo estaba vacío
  - fix aplicado: el procesador ahora sobrescribe `encf`, `track_id`, `codigo_seguridad` y `xml_firmado/xml_respuesta` con el intento vigente
- Hallazgo de consistencia de secuencia durante pruebas dev:
  - al volver a reemitir `ECF#5`, DGII rechazó `E310000000002` con `Este número de secuencia ya ha sido utilizado.`
  - lectura más probable: el sistema local no había quedado enterado oportunamente de que `E310000000002` ya se había usado en un intento previo, debido al bug anterior de persistencia del `encf`
  - interpretación: esto luce como artefacto del troubleshooting en development sobre el mismo `ECF`, no como comportamiento esperado del flujo limpio
  - consecuencia práctica: para seguir validando tipo `31`, conviene probar con una venta nueva y una secuencia nueva
- Hallazgo posterior en venta limpia tipo `31`:
  - DGII respondió `Fecha de vencimiento de secuencia inválida.`
  - causa más probable: para este emisor/entorno, `FechaVencimientoSecuencia` no coincide con la fecha realmente válida del rango autorizado y no conviene inferirla automáticamente como `31-12-año_actual`
  - fix aplicado: el builder ahora soporta `fecha_vencimiento_secuencia` configurable desde `Emisor.config_proveedor`
- Ajuste posterior con ejemplo oficial de MSeller para tipo `31`:
  - se alineó mejor `IdDoc` con campos configurables desde `config_proveedor`: `indicador_envio_diferido`, `tipo_ingresos`, `tipo_pago`, `fecha_limite_pago`, `fecha_vencimiento_secuencia`
  - se agregaron `MontoExento=0` y `MontoNoFacturable=0` explícitos para tipo `31`
  - se agregó bloque `Paginacion` de una página y `FechaHoraFirma=""` para acercar el payload al ejemplo de referencia
- Hallazgo posterior en `Totales` para tipo `31`:
  - DGII respondió `The element 'Totales' has invalid child element 'MontoExento'...`
  - causa identificada en código: el orden dentro de `Totales` no seguía el orden del ejemplo oficial; `ITBIS1` se serializaba antes de `MontoExento`
  - fix aplicado: `_build_totales()` ahora serializa en orden más alineado con MSeller: gravados -> exento -> ITBIS porcentuales -> ITBIS totales -> monto total -> monto no facturable
- Hallazgo posterior con `FechaHoraFirma` en tipo `31`:
  - una venta nueva procesada por el flujo real fue rechazada con `El campo FechaHoraFirma de la sección FechaHoraFirma no es válido.`
  - causa identificada en código: se estaba enviando `FechaHoraFirma=""` siguiendo un ejemplo de referencia
  - fix aplicado: se dejó de enviar `FechaHoraFirma` vacío; el builder ahora omite ese campo para tipo `31`
- Ajuste posterior de estrategia para tipo `31`:
  - se adoptó una variante minimalista del payload para troubleshooting
  - se eliminó para tipo `31` el envío de `Paginacion`, `TotalPaginas`, `MontoNoFacturable` y `MontoExento=0`
  - `IndicadorEnvioDiferido` dejó de depender de una config global y ahora usa default por tipo: `31 -> 1`, `32/34 -> 0`
  - hipótesis descartada: se probó temporalmente `IndicadorEnvioDiferido=0` para tipo `31`, pero DGII respondió explícitamente que en ese contexto no se permiten valores distintos de `1`. Se revierte el builder a default `1` para `31`
  - nuevo hallazgo desde el portal/foro DGII: para documentos cuyos `MontoItem` representan base gravable sin ITBIS incluido, `Encabezado.IdDoc.IndicadorMontoGravado` debe ir explícitamente en `0`. Con base en eso, el builder de tipo `31` vuelve a enviar `IndicadorMontoGravado=0`
  - resultado final exitoso: una nueva emisión tipo `31` fue aceptada por DGII con `encf=E310000000013`, `estado='Aceptado'` y `secuenciaUtilizada=true`
  - combinación validada para tipo `31` en este entorno:
    - `FechaVencimientoSecuencia=31-12-2028`
    - `IndicadorEnvioDiferido=1`
    - `IndicadorMontoGravado=0`
    - `TipoIngresos=01`
    - `TipoPago=1`
  - XML firmado aceptado:
    - sin `Paginacion`
    - sin `TotalPaginas`
    - sin `MontoExento` cuando no aplica
    - con `FechaHoraFirma` generado por MSeller
  - hallazgo interpretativo: la combinación `IndicadorEnvioDiferido=1` + `IndicadorMontoGravado=0` resultó ser la clave para destrabar tipo `31` en el flujo actual
- Observación sobre la consulta pública DGII en documentos rechazados:
  - durante rechazos previos de tipo `31`, el portal mostró `Razón social comprador = -` y `Total de ITBIS = -` aun cuando esos datos sí estaban presentes en el XML firmado
  - interpretación más probable: en estado `Rechazado`, la consulta pública expone una representación parcial del documento y no conviene usarla como prueba de ausencia de campos en el payload original
- Aclaración importante sobre `validate=true`:
  - según documentación/comentario de MSeller, `validate=true` no debería generar un documento real
  - en TesteCF, la respuesta observada sigue retornando artefactos tipo `ecf`, `internalTrackId`, `securityCode`, `qr_url` y `signedDate`
  - interpretación operativa: aunque el proveedor lo describa como validación, el sandbox devuelve suficiente metadata como para no tratar esa llamada como "invisible" dentro del troubleshooting
- Fase B validada de extremo a extremo a nivel local para una venta real de contado:
  - venta usada: `id=23`, `VENTA-20260423-00001`
  - `comprador=None` correcto para cliente contado genérico
  - totales del mapper cuadran exactamente con el total POS
  - payload tipo `32` con estructura consistente
  - `encolar_emision()` crea `ECF` y `EventoECF` correctos
- Fase C validada contra TesteCF:
  - auth real funcionando
  - emisión real funcionando
  - consulta real funcionando
  - TesteCF respondió `Aceptado Condicional` con mensaje DGII `Número de secuencia no autorizada`
- Fase D validada end-to-end por shell:
  - venta usada: `id=22`, `VENTA-20260422-00001`
  - `ECF id=2`
  - transición completa observada: `PENDIENTE -> ENVIADO -> APROBADO`
  - persistencia confirmada de `encf`, `track_id`, `codigo_seguridad`, `xml_firmado` y `xml_respuesta`
- Fase E validada parcialmente desde GUI/POS:
  - estado final observado: `APROBADO`
  - badge de `venta_exitosa` funcionando
  - `eNCF` correcto mostrado en UI
  - polling funcional contra el endpoint de estado
- Validación adicional de tipo `31`:
  - DGII respondió `Aceptado` para `E310000000013`
  - el XML firmado visible en MSeller confirmó que el documento aceptado conserva `FechaHoraFirma` y `Signature` generados por el proveedor
  - el caso aprobado incluyó `CantidadItem=2`, lo cual también ayuda a descartar que el rechazo previo estuviera relacionado simplemente con cantidad mayor que `1`
- Hallazgo importante: `validate=true` no devolvió un simple resultado de validación; devolvió una respuesta completa de documento con `ecf`, `track`, `securityCode`, `qr_url` y `signedDate`.
- Hallazgo importante: el procesador también emitió usando `params={'validate': 'true'}` y aun así obtuvo un documento consultable que terminó en `APROBADO`. Eso indica que hoy la configuración `validar_antes_enviar=True` no está rompiendo TesteCF, pero deja ambigüedad conceptual para producción y conviene revisarlo antes del piloto.
- Smoke test posterior al cambio de código:
  - venta usada: `id=20`, `VENTA-20260214-00003`
  - `ECF id=4`
  - primera ejecución del command: `PENDIENTE -> ENVIADO`
  - segunda ejecución del command: `ENVIADO -> APROBADO`
  - log confirmado: `MSeller POST ... params=None`
- Hallazgo importante resuelto: tras forzar `validar=False` en `MSellerEmisor.emitir()`, la emisión normal ya no hereda `validate=true` desde configuración y el flujo sigue funcionando correctamente.
- Decisión posterior a las pruebas: el flujo normal de emisión fue ajustado para forzar `validar=False` en `MSellerEmisor.emitir()`. `validate=true` queda reservado para pruebas/manual debugging.
- Hallazgo importante: al probar el método privado `_authenticate()` por separado, la siguiente request volvió a autenticar; para pruebas de caché/token no usar `_authenticate()` como si representara el flujo normal.
- Hallazgo menor: cada llamada a `procesar_ecf()` reautenticó contra MSeller. Esto es esperable con la arquitectura actual porque cada ejecución construye una nueva instancia de `MSellerEmisor` y, por tanto, un nuevo `MSellerHTTPClient`.
- Hallazgo menor: los `EventoECF.fecha` impresos en shell aparecen en UTC (`+00:00`). No implica error funcional por sí mismo porque el proyecto usa `USE_TZ=True`, pero conviene recordar esa diferencia al comparar con hora local de Santo Domingo.
- Hallazgo menor: en la prueba GUI el badge ya cargó con estado final resuelto, así que quedó validado el estado final y el polling, pero no todavía una transición visual observada en tiempo real desde estados intermedios.

---

## 8. Recomendaciones de ejecución

1. Empezar por Fase B y no tocar MSeller hasta que mapper/payload/cola se vean correctos.
2. En Fase C usar eNCFs de prueba únicos para no contaminar la secuencia local del procesador.
2.1. No asumir que `validate=true` es un dry-run inocuo en TesteCF; tratarlo como operación con efectos hasta tener confirmación más fuerte.
3. Para Fase D usar una venta de prueba dedicada si quieres que la trazabilidad quede limpia.
4. No mezclar pruebas de tipo `31` hasta validar establemente `32`.
5. Si aparece un rechazo, guardar completo:
   - `ECF.id`
   - `eNCF`
   - `estado`
   - `EventoECF.payload`
   - `xml_respuesta`
6. Si la emisión HTTP directa funciona pero el procesador falla, el problema probablemente está en la orquestación/persistencia y no en MSeller.
7. Mantener `validate=true` como herramienta de prueba explícita y no como default implícito del flujo real de emisión.
