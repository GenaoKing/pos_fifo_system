# Visión de Producto 2026 — Dónde está el mayor valor a futuro

**Fecha:** 2 junio 2026
**Autor:** Análisis estratégico sobre el estado real del código + normativa DGII vigente
**Estado:** Documento de discusión. No es un roadmap de ejecución todavía; es el insumo para decidir cuál construir.

> Este documento mira *por encima* de los roadmaps técnicos existentes
> ([ROADMAP_CLOUD](ROADMAP_CLOUD.md), [ROADMAP_PORTAL](ROADMAP_PORTAL.md),
> [ROADMAP_DEPLOY_AZURE](ROADMAP_DEPLOY_AZURE.md),
> [ROADMAP_ECF_FASE_INICIAL](ROADMAP_ECF_FASE_INICIAL.md)). Esos dicen *cómo*
> construir lo ya decidido. Este pregunta *qué deberíamos construir después* y
> *por qué*, anclado a lo que el negocio del cliente realmente necesita en
> República Dominicana.
>
> **Companion:** [OPORTUNIDADES_INNOVACION.md](OPORTUNIDADES_INNOVACION.md) abre
> el abanico con productos *nuevos* más allá del cumplimiento (copilot por
> WhatsApp, reorden predictivo, capital de trabajo embebido sobre ingreso
> verificado por e-CF, benchmarking entre negocios, etc.). Este documento es la
> tesis fiscal; aquel es la exploración abierta.

---

## 0. Cómo se hizo este análisis

Se revisó:

- La lógica de negocio real en el código: `apps/inventario/models.py`
  (Compra/DetalleCompra/Lote/FIFO), `apps/productos/models.py`,
  `apps/facturacion_electronica/services/venta_to_ecf.py` (desglose fiscal de
  ventas), `apps/cuentas_por_cobrar` (CxC).
- Los cuatro roadmaps vigentes y la sección "Fuera de scope" del portal.
- La normativa dominicana vigente a junio 2026: Ley 32-23 de facturación
  electrónica y los formatos de envío DGII 606/607/608 (ver §10 Fuentes).

La conclusión no salió de una lluvia de ideas: salió de cruzar **lo que el
código ya hace** con **lo que la DGII exige** y ver dónde está el hueco.

---

## 1. Resumen ejecutivo — La tesis

**El mayor valor a futuro no son más features de POS. Es convertir el sistema en
la herramienta de cumplimiento fiscal y contable del cliente.** Una PYME
dominicana ya tiene de quién comprar un POS; lo que le quita el sueño cada mes es
la DGII.

Las dos ideas que disparan este análisis —*registrar facturas de compra por foto
con IA* y *libros contables según reglas de RD*— **no son dos features
separadas: son una sola línea de producto**, y se necesitan mutuamente:

```
  Foto de factura de proveedor
        │  (IA extrae: RNC, NCF, fecha, ITBIS, líneas, total)
        ▼
  Compra enriquecida fiscalmente  ──►  Lotes FIFO con costo real (ya existe)
        │                          └─►  Datos para el Reporte 606 (HOY IMPOSIBLE)
        ▼
  Reportes DGII 606 / 607 / 608 listos para subir a la Oficina Virtual
        │
        ▼
  Inteligencia fiscal: ITBIS a pagar proyectado, margen real, flujo de caja
```

- La **captura por foto + IA (§4.1)** es la *cuña*: elimina la digitación manual,
  que es justo la razón por la que hoy las compras se registran sin datos
  fiscales.
- Los **libros DGII 606/607/608 (§4.2)** son el *premio*: es lo que el cliente
  paga con gusto porque hoy se lo hace un contador a mano o en Excel.
- El sistema **ya tiene medio camino hecho del lado ventas** (e-CF desglosa ITBIS
  y emite tipos 31/32/34 → eso alimenta el 607 y el 608). **El lado compras está
  fiscalmente vacío** (→ el 606 es imposible hoy). La IA cierra ese hueco.

Y hay un reloj corriendo: la facturación electrónica **es obligatoria para
pequeños/micro contribuyentes el 15 de noviembre de 2026** (Ley 32-23). Eso
convierte a e-CF de "feature del horizonte" en "fecha de entrega" (§4.3).

**Recomendación de una línea:** priorizar la línea *Cumplimiento & Contabilidad*
(e-CF completo → 606 por foto+IA → libros 606/607/608 → inteligencia de ITBIS)
por encima de cualquier otra expansión, porque es la única que crea un *moat*
real (lock-in) y resuelve un dolor con fecha límite legal.

---

## 2. Dónde está el negocio hoy (anclado al código)

Lo que **ya funciona** y es base sólida:

| Capacidad | Dónde vive | Estado |
|---|---|---|
| FIFO real con lotes por compra | `apps/inventario/models.py` | Sólido. 1 `DetalleCompra` = 1 `Lote` + `MovimientoLote` |
| Costo unitario por lote | `Lote.costo_unitario` | Sólido. Base para margen real |
| Ventas + pagos múltiples + POS | `apps/ventas` | En producción (Royal Plast) |
| Crédito y cartera (CxC) | `apps/cuentas_por_cobrar` | v1 completo, read-only en portal |
| Desglose fiscal de ventas (ITBIS 18/16/exento) | `venta_to_ecf.py` | Sólido. Tipos 31/32/34 |
| e-CF vía PSFE (MSeller) + librería nativa | `apps/facturacion_electronica` | Fase inicial; nativa en paralelo |
| Portal cloud (React) multi-sucursal | `pos-cloud-dashboard` + `apps/api` | Reportes, maestros, CxC read-only |
| Sync por eventos sucursal→cloud | `apps/sync` | `VENTA_*`, `CXC_*` |

Lo que **NO existe** y bloquea la visión fiscal (ver §9 para el detalle por
archivo):

1. **No hay modelo `Proveedor`.** `Compra.proveedor` es un `CharField` de texto
   libre (`apps/inventario/models.py:18`). No hay RNC, no hay forma de agrupar
   compras por suplidor.
2. **La compra no captura datos fiscales.** `numero_factura` es texto libre y
   opcional; no hay NCF/e-NCF, ni tipo de NCF, ni **ITBIS desglosado**, ni
   clasificación bien/servicio. Es exactamente lo que el **606 exige**.
3. **ITBIS es global, no por producto.** `venta_to_ecf.py:56` lo resuelve desde
   `ConfiguracionNegocio.itbis_porcentaje_global` con un `TODO` explícito para
   `Producto.itbis_pct`. Una canasta mixta (gravado + exento) hoy no se modela
   bien del lado compras.
4. **No hay capa contable.** Ningún app genera 606/607/608 ni concilia ITBIS
   adelantado vs. cobrado (IT-1).

El dato importante: **el lado ventas ya está al 70% del 607** (el e-CF calcula
gravado 18/16, exento e ITBIS por venta). El lado compras está en 0% del 606. La
asimetría es la oportunidad.

---

## 3. El lente: qué genera valor *defendible* para una PYME dominicana

No todo lo que se puede construir vale lo mismo. Filtré las ideas por cuatro
criterios:

1. **¿Resuelve un dolor que el cliente ya paga por resolver?** (contador,
   digitador, multas DGII). → Alto valor.
2. **¿Crea lock-in / moat?** Si la data fiscal e histórica vive aquí, cambiarse
   de sistema duele. → Retención.
3. **¿Aprovecha lo que ya tenemos** (FIFO, e-CF, costo por lote, sync, portal)
   en vez de empezar de cero? → Menor costo, mayor coherencia.
4. **¿Tiene urgencia externa** (fecha legal, fiscalización)? → Disposición a
   pagar *ahora*.

La línea *Cumplimiento & Contabilidad* puntúa alto en los cuatro. Las features
de "más POS" (más métodos de pago, más reportes de ventas) puntúan alto solo en
el criterio 3. Por eso la recomendación se inclina a lo fiscal.

---

## 4. Oportunidad central: "Cumplimiento & Contabilidad sin fricción"

### 4.1 Captura de facturas de compra por foto + IA — *la cuña* ⭐

**El dolor.** Hoy registrar una compra es digitar a mano proveedor, factura,
cada línea, cantidad y costo (`DetalleCompra`). Es tan tedioso que, en la
práctica, se registra lo mínimo para que el FIFO tenga costo, y **se pierden los
datos fiscales** (RNC del proveedor, NCF, ITBIS). Sin esos datos, el 606 no se
puede armar.

**Lo que ya existe a favor.** El "destino" del dato ya está construido: registrar
una `Compra` con sus `DetalleCompra` **auto-genera lotes FIFO y movimientos**
(`apps/inventario/models.py:144` `_crear_lote`). Solo falta *llenar ese
formulario sin digitar*.

**Importante:** esta idea **ya estaba en el radar** — aparece en
[ROADMAP_PORTAL.md](ROADMAP_PORTAL.md) → "Fuera de scope (Fase 6+)": *"IA para
escaneo de facturas de compra (modelo evaluado, no implementado)"*. Este
documento argumenta **subirla de prioridad**, porque es la pieza que habilita
todo lo demás.

**La solución.**

```
 [Foto / PDF de la factura del proveedor]
        │  (cámara del POS, móvil, o subida en el portal)
        ▼
 Servicio de extracción en la nube  ── usa un modelo de visión (Claude vision /
        │                              Document AI) con un prompt que devuelve
        │                              JSON estructurado y tipado
        ▼
 Borrador de Compra  { proveedor:{nombre,rnc}, ncf, tipo_ncf, fecha,
        │              itbis_total, lineas:[{descripcion, cantidad, costo,
        │              itbis_linea}], total, confianza_por_campo }
        ▼
 Revisión humana (human-in-the-loop)  ── el usuario confirma/corrige; los campos
        │                                de baja confianza se resaltan
        ▼
 Commit  ──►  crea Compra + DetalleCompra + Lote (FIFO, ya existe)
         └─►  crea ComprobanteCompra (datos fiscales para el 606)
         └─►  emite evento COMPRA_REGISTRADA al cloud (alimenta el 606 consolidado)
```

**Encaje arquitectónico (respeta las decisiones ya tomadas):**

- La compra **crea stock físico local** → la `Compra` sigue siendo un evento
  *local* de la sucursal (igual que la venta), no un maestro cloud. El sync
  sucursal→cloud por eventos ya existe; se agrega un tipo `COMPRA_REGISTRADA`
  análogo a `VENTA_CREADA` (`apps/sync/constants.py`).
- La IA necesita nube → el flujo es: la sucursal/portal **sube la foto a la API
  cloud**, el servicio de visión extrae, devuelve el borrador, el humano
  confirma, y *recién ahí* se materializa la compra local. Coherente con "el
  cloud agrega capacidad; el POS sigue operando si está offline" (si no hay
  conexión, se cae al registro manual de siempre).
- El `Proveedor` sí es un **maestro** (como `Cliente`) → vive en el cloud como
  fuente de verdad y se propaga por `pull_maestros` (mismo patrón que productos).

**Realidad de la IA (no venderla mágica).** Las facturas de proveedor dominicanas
son un zoo: impresas, térmicas, manuscritas, fotos torcidas. Por eso el diseño es
**human-in-the-loop obligatorio**, no extracción ciega:

- El modelo devuelve **confianza por campo**; los dudosos se marcan para revisión.
- El RNC se **valida** (formato 9/11 dígitos; idealmente contra el padrón DGII).
- Los totales se **cuadran** (suma de líneas + ITBIS == total) antes de permitir
  el commit — el mismo rigor de redondeo que ya aplica `venta_to_ecf.py` (`_q`,
  half-up).
- Se guarda la **imagen original** ligada a la compra (soporte ante
  fiscalización + reentreno futuro).

**Valor:** (1) ahorra el tiempo de digitación — el gancho que el dueño *siente*;
(2) **captura la data fiscal que el 606 necesita** — el gancho que lo *retiene*;
(3) mejora la exactitud del costo FIFO (menos errores de tecleo).

**Esfuerzo grueso:** 3–5 semanas. Modelo `Proveedor` + `ComprobanteCompra`
(0.5 sem), pipeline de extracción + prompt + validación (1–1.5 sem), UI de
revisión (1–1.5 sem), evento de sync + 606 hookup (0.5–1 sem).

**Dependencias:** ninguna dura. Se puede empezar ya. Se beneficia del portal
cloud existente para la pantalla de revisión.

---

### 4.2 Libros / reportes DGII 606 · 607 · 608 — *el premio* ⭐

**El dolor.** Todo contribuyente con NCF debe remitir mensualmente a la DGII,
**dentro de los primeros 15 días del mes siguiente**:

- **606 — Compras de bienes y servicios:** RNC/cédula del suplidor, tipo de
  identificación, NCF, NCF modificado (si aplica), fecha del comprobante, fecha
  de pago, montos de bienes y servicios, **ITBIS facturado**, ITBIS retenido,
  retención de ISR, etc.
- **607 — Ventas de bienes y servicios:** NCF emitidos, notas de crédito/débito,
  datos del cliente, montos, **ITBIS cobrado**, retenciones de terceros.
- **608 — Comprobantes anulados:** NCF anulados + razón de anulación.

(Incluso *sin operaciones* hay que remitir los tres en cero.) Hoy esto lo hace un
contador externo o el dueño peleando con Excel. **Si el sistema lo genera listo
para subir, se vuelve indispensable.**

**Lo que ya existe a favor.**

- **607:** prácticamente derivable. El e-CF ya calcula por venta el desglose
  gravado 18/16, exento e ITBIS (`venta_to_ecf.py`), y se conoce el NCF/e-NCF
  emitido y el cliente.
- **608:** derivable de las anulaciones + e-CF tipo 34 (nota de crédito) que ya
  modela el mapper.
- **606:** **imposible hoy** — depende de §4.1 para tener RNC de proveedor, NCF e
  ITBIS de compra.

**La solución.** Un módulo `apps/contabilidad` (o `apps/reportes_fiscales`) que:

1. Genera cada formato en el **layout exacto que la DGII espera** (archivo de
   envío TXT/separado por pipes según el instructivo vigente — el del 606 se
   actualizó en febrero 2026, así que el formato se versiona).
2. Se expone en el **portal cloud** como "Reportes DGII del mes": el dueño elige
   período, ve un preview cuadrado, y descarga el archivo listo para la Oficina
   Virtual.
3. Concilia: el total de ITBIS del 607 menos el ITBIS adelantado del 606 = base
   de la declaración **IT-1** (ver §4.4).

**Encaje arquitectónico:** es *reportería*, y el portal ya tiene una capa
query-based cloud (`apps/api/services/reporting.py`) que es justo el lugar
correcto — no reusar `ReporteManager` local (misma decisión ya tomada para
reportes multi-sucursal). El 607/608 se construyen desde los e-CF/ventas
consolidados en cloud; el 606 desde los eventos `COMPRA_REGISTRADA`.

**Valor:** convierte el POS en la herramienta que **le ahorra al cliente el
contador-digitador y el riesgo de multa**. Es el moat: una vez que tres meses de
606/607/608 viven aquí, migrar a otro sistema es impensable.

**Esfuerzo grueso:** 607+608 (2–3 sem, casi todo es formato y casos borde de
ITBIS/notas). 606 (1–2 sem *después* de §4.1). IT-1/conciliación (1 sem).

**Dependencias:** 606 depende de §4.1. 607/608 se pueden empezar en paralelo con
el e-CF completo (§4.3).

---

### 4.3 e-CF completo — *el reloj que ya está corriendo* ⏰

No es una idea nueva (ya tiene su [roadmap](ROADMAP_ECF_FASE_INICIAL.md)), pero
el calendario legal **reordena la prioridad de todo lo demás**:

- Grandes contribuyentes: obligatorio desde mayo 2024.
- Grandes locales / medianos: **15 noviembre 2025**.
- **Pequeños / micro / no clasificados: 15 noviembre 2026.** ← Royal Plast y la
  mayoría de los clientes objetivo caen aquí.

Eso son **~5 meses desde hoy**. Sanciones por incumplir: multas de 5 a 50
salarios mínimos, cierre temporal. Y la Ley 32-23 *premia* emitir e-CF con
crédito de ITBIS, exención de retención y deducibilidad de gastos — un argumento
de venta, no solo de cumplimiento.

**Implicación para este documento:** terminar el camino e-CF (MSeller en
producción para el piloto, luego el resto de clientes) es **prerrequisito
temporal** de la línea fiscal. No tiene sentido vender "libros 606/607/608" a un
cliente que aún no emite e-CF. La secuencia natural es: **e-CF operativo → 607/608
casi gratis → 606 por foto+IA → inteligencia fiscal.**

---

### 4.4 Inteligencia fiscal — *el diferenciador que ningún Excel da*

Una vez que 606 y 607 existen en el sistema, aparece algo que el contador solo
entrega *después* del cierre de mes: **proyección en vivo**.

- **ITBIS a pagar proyectado (IT-1):** ITBIS cobrado en ventas (607) − ITBIS
  adelantado en compras (606) = lo que probablemente toca pagar este mes. El
  dueño lo ve el día 12, no el día 28.
- **Margen real por producto/categoría:** ya hay costo por lote
  (`Lote.costo_unitario`) y precio de venta — la utilidad bruta real es
  calculable hoy y casi nadie la mira.
- **Flujo de caja:** entradas (cobros CxC, ya modelados) − salidas (pagos a
  proveedores, §5.1) → posición de caja proyectada.

**Valor:** pasa de "sistema que registra el pasado" a "sistema que avisa del
futuro". Es lo que justifica un tier premium del SaaS.

**Esfuerzo:** 1–2 sem *encima* de 606+607. Es agregación, no plomería nueva.

---

## 5. Oportunidades complementarias (segundo anillo)

### 5.1 Proveedores + Cuentas por Pagar (CxP) — el espejo de CxC

El sistema ya modela lo que **me deben** (CxC, `apps/cuentas_por_cobrar`). El
hueco simétrico es lo que **yo debo** a proveedores. §4.1 ya obliga a crear el
modelo `Proveedor`; sobre él, un CxP es natural:

- Compras a crédito → saldo por proveedor → vencimientos → "¿a quién le toca
  pagar esta semana?".
- Reusa el patrón ya probado de CxC (cuotas, abonos, aging) casi 1:1.
- Cierra el flujo de caja real (CxC entra − CxP sale) para §4.4.

**Esfuerzo:** 2–3 sem reusando el dominio CxC. **Depende de** §4.1 (modelo
Proveedor). Valor medio-alto; bajo riesgo por ser un patrón conocido.

### 5.2 ITBIS por producto + analítica de margen

Cerrar el `TODO` de `venta_to_ecf.py:56`: `Producto.itbis_pct` (cae al global si
es `None`). Necesario para canastas mixtas gravado/exento y para que el 606/607
sean exactos cuando el cliente vende productos de tasas distintas. Pequeño, pero
es deuda fiscal latente. **Esfuerzo:** 2–4 días.

### 5.3 Companion móvil / WhatsApp para el dueño

Ya está en el horizonte del [ROADMAP_CLOUD](ROADMAP_CLOUD.md) ("App móvil para el
dueño"). Mi único aporte estratégico: el canal de mayor adopción en RD para una
PYME no es una app nativa, es **WhatsApp**. Un bot que mande el cierre del día y
alertas (stock bajo, cuota CxC vencida, "ya casi toca el 606") tiene más tracción
que una app que hay que instalar. Pero es *capa de presentación* sobre datos que
los puntos anteriores ya producen — **no construir esto antes que la data que lo
alimenta.**

---

## 6. Secuencia recomendada y dependencias

```
        ┌─────────────────────────────────────────────────────────┐
 RELOJ  │  e-CF completo en producción (§4.3)   ── 15 nov 2026     │
        └───────────────┬─────────────────────────────────────────┘
                        │ habilita
        ┌───────────────▼──────────────┐     ┌───────────────────┐
        │  607 + 608 (§4.2)            │     │  Captura foto+IA   │
        │  (casi derivable del e-CF)   │     │  de compras (§4.1) │ ⭐ cuña
        └───────────────┬──────────────┘     └─────────┬─────────┘
                        │                               │ produce data fiscal
                        │                               ▼
                        │                     ┌───────────────────┐
                        │                     │  Modelo Proveedor  │
                        │                     │  + ComprobanteCompra│
                        │                     └─────────┬──────────┘
                        │                               │ habilita
                        │              ┌────────────────┼────────────────┐
                        ▼              ▼                ▼                ▼
                 ┌──────────────────────────┐  ┌──────────────┐  ┌──────────────┐
                 │  606 (§4.2)              │  │ CxP (§5.1)   │  │ ITBIS x prod │
                 └────────────┬─────────────┘  └──────┬───────┘  │   (§5.2)     │
                              │                       │          └──────────────┘
                              ▼                       ▼
                 ┌──────────────────────────────────────────────┐
                 │  Inteligencia fiscal: IT-1, margen, caja (§4.4)│
                 └───────────────────────┬──────────────────────┘
                                         ▼
                              ┌──────────────────────┐
                              │ WhatsApp companion §5.3│
                              └──────────────────────┘
```

**Orden sugerido (no es compromiso de fechas):**

1. **Cerrar e-CF para el piloto** (ya en curso). Reloj legal.
2. **607 + 608** en el portal — cosecha barata sobre el e-CF.
3. **Captura foto+IA + modelo Proveedor** — la cuña; valor visible inmediato.
4. **606** — ahora sí es posible; cierra el trío DGII.
5. **Inteligencia fiscal (IT-1, margen, caja)** — el diferenciador premium.
6. **CxP, ITBIS por producto, WhatsApp** — segundo anillo, según tracción.

---

## 7. Riesgos y mitigaciones

| Riesgo | Mitigación |
|---|---|
| Exactitud de la IA en facturas reales (térmicas, manuscritas, fotos malas) | Human-in-the-loop obligatorio; confianza por campo; cuadre de totales antes del commit; guardar imagen original |
| Responsabilidad legal del reporte | El sistema **genera y cuadra**; el cliente **revisa y remite**. Dejar claro en UI y términos que el contribuyente es el responsable ante DGII |
| El formato DGII cambia (el 606 se actualizó feb-2026) | Versionar el generador de formato; tests de contrato contra el instructivo vigente; no hardcodear el layout en una sola función |
| Costo por llamada de visión IA por factura | Volumen real de una PYME es bajo (decenas/mes); cachear; permitir lote; medir costo/factura desde el día 1 |
| Dependencia de conexión para la IA | Fallback al registro manual existente cuando esté offline; el POS nunca se bloquea |
| RNC de proveedor inválido/informal | Validar formato; soportar "proveedor informal" como la DGII lo contempla en el 606; idealmente validar contra padrón |
| Distrae del reloj e-CF | e-CF va primero; lo fiscal-contable se apoya en él, no compite |

---

## 8. Cómo se mide el éxito

- **§4.1:** % de compras registradas vía foto vs. manual; tiempo medio de
  registro de una compra (objetivo: de minutos a < 30 s de revisión); tasa de
  corrección por campo.
- **§4.2:** nº de clientes que descargan su 606/607/608 desde el portal;
  reducción de horas de contador reportadas por el dueño.
- **§4.3:** clientes emitiendo e-CF antes del 15-nov-2026; tasa de aprobación DGII.
- **§4.4:** clientes que abren el panel de "ITBIS proyectado" cada mes (proxy de
  valor percibido).
- **Negocio:** retención (un cliente con 3 meses de libros aquí no se va) y
  disposición a pagar tier fiscal.

---

## 9. Apéndice — Hallazgos técnicos concretos (los huecos en el código)

Para que el próximo que ejecute no los re-descubra:

1. **No existe modelo `Proveedor`.** Confirmado por inventario de `models.py`
   (no aparece). `apps/inventario/models.py:18` — `Compra.proveedor =
   CharField(max_length=200)` es texto libre.
2. **`Compra` no tiene campos fiscales.** `apps/inventario/models.py:8-88`:
   solo `numero_compra`, `proveedor` (str), `fecha_compra`, `numero_factura`
   (str libre, opcional), `total`, `notas`, `usuario`, `sucursal`. Falta:
   RNC proveedor, NCF/e-NCF, tipo de NCF/comprobante, ITBIS desglosado,
   clasificación bien/servicio, fecha de pago. **Todo lo que el 606 exige.**
3. **`DetalleCompra` no separa ITBIS.** `apps/inventario/models.py:91-181`:
   `cantidad`, `costo_unitario`, `subtotal`. El costo entra entero al lote FIFO;
   el ITBIS de compra no se modela (y es lo que va al 606 y al crédito de ITBIS).
4. **ITBIS es global, no por producto.** `venta_to_ecf.py:56-66` con `TODO`
   explícito para `Producto.itbis_pct`. `apps/productos/models.py` no tiene el
   campo todavía.
5. **El lado ventas ya está maduro fiscalmente.** `venta_to_ecf.py` desglosa
   gravado 18/16, exento, ITBIS por línea y total; emite tipos 31/32/34. Es la
   base lista del 607/608.
6. **El sync por eventos es el canal correcto para llevar compras al cloud.**
   `apps/sync` ya hace `VENTA_*`/`CXC_*`; agregar `COMPRA_REGISTRADA` es el
   patrón natural para alimentar el 606 consolidado.
7. **Idea ya contemplada, no priorizada:** [ROADMAP_PORTAL.md](ROADMAP_PORTAL.md)
   → "Fuera de scope (Fase 6+)": *"IA para escaneo de facturas de compra (modelo
   evaluado, no implementado)"*. Este documento propone re-priorizarla por su rol
   de cuña.

**Boceto de modelo (no normativo, para aterrizar la conversación):**

```python
# apps/proveedores/models.py  (nuevo maestro, fuente de verdad en cloud)
class Proveedor(models.Model):
    rnc = ...            # validado, índice
    nombre = ...
    tipo_identificacion = ...   # RNC | Cédula | (informal)
    activo = ...
    fecha_modificacion = ...    # auto_now → se propaga por pull_maestros

# apps/inventario/models.py  (extender Compra, no romper lo existente)
class ComprobanteCompra(models.Model):   # 1:1 con Compra, datos para el 606
    compra = OneToOne(Compra)
    proveedor = FK(Proveedor)            # reemplaza el str a futuro (nullable en migración)
    ncf = ...                            # NCF/e-NCF del proveedor
    tipo_ncf = ...                       # 01/11/... según comprobante
    fecha_comprobante = ...
    fecha_pago = ...
    monto_bienes = ...; monto_servicios = ...
    itbis_facturado = ...; itbis_retenido = ...; isr_retenido = ...
    imagen_original = ...                # soporte fiscal + reentreno IA
    confianza_extraccion = JSONField     # por campo, de la IA
```

El campo `Compra.proveedor` (str) se mantiene como *legacy* y se migra a
`Proveedor` con `null=True` — mismo principio incremental del resto del proyecto
("no reescribir, agregar").

---

## 10. Fuentes (normativa DGII, verificadas jun-2026)

- DGII — Listados de contribuyentes obligados a e-CF:
  https://dgii.gov.do/cicloContribuyente/facturacion/comprobantesFiscalesElectronicosE-CF/Paginas/Listados-contribuyentes-obligados-implementar-facturacion-electronica.aspx
- DGII — Formatos de envío de datos (606/607/608/609):
  https://dgii.gov.do/cicloContribuyente/obligacionesTributarias/remisionInformacion/Paginas/formatoEnvioDatos.aspx
- DGII — Instructivo de llenado y envío del Formato 606 (compras):
  https://dgii.gov.do/publicacionesOficiales/bibliotecaVirtual/contribuyentes/formatoEnvioDatos/Documents/4-LlenadoyEnvioFormato606.pdf
- DGII — Instructivo de llenado y envío del Formato 607 (ventas):
  https://dgii.gov.do/publicacionesOficiales/bibliotecaVirtual/contribuyentes/formatoEnvioDatos/Documents/5-InstructivoLlenadoyenvioFomato607.pdf
- Resumen Ley 32-23 y calendario de obligatoriedad (referencia secundaria):
  https://thefactoryhka.com.do/ley-32-23-y-la-obligatoriedad-de-factura-electronica-fechas-clave-y-todo-lo-que-debes-saber/
- Reportes 606/607/608 — guía práctica (referencia secundaria):
  https://blog.alegra.com/republica-dominicana/reportes-contables-606-607-608/

> Antes de implementar el generador de formatos, **descargar y versionar los
> instructivos PDF vigentes** en `docs/dgii/` (el del 606 se actualizó
> feb-2026). El layout exacto manda; este documento da la estrategia, no el
> campo-por-campo.
