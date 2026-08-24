# Runbook — Actualizar un cliente existente y reparar su sync

Procedimiento para llevar una instalación ya operativa a la versión actual y
**recuperar los datos que se perdieron** con los defectos de la versión anterior.

> **Cómo usar este documento.** Pasos numerados, un comando exacto por paso, y la
> salida esperada de cada uno. Ejecutar en orden. Si un paso no da lo esperado,
> buscar el síntoma en la tabla de fallos antes de improvisar.
>
> Escrito para que lo ejecute igual una persona o un agente de IA. Lleva además
> una hoja de registro al final: lo que se anote ahí es lo que alimenta la
> próxima versión de este runbook.

**Para una instalación NUEVA**, usar `INSTALACION_CLIENTE_NUEVO.md`.

---

## 0. Antes de tocar nada

| Requisito | Comando | Esperado |
|---|---|---|
| Administrador | `net session` | sin error |
| Fuera de horario de venta | — | el negocio cerrado |
| Paquete nuevo copiado | `dir C:\pos_actualizacion\manage.py` | existe |
| Cliente sincronizando | `sc query POSFifoSync` | `STATE : 4 RUNNING` |

> **`actualizar.bat` respalda la BD completa antes de migrar** y aborta si el
> backup falla. Ese backup es la red de seguridad de todo lo que sigue.

---

## 1. Fotografía del estado ANTES (solo lectura)

Sirve para dos cosas: saber qué reparar, y poder demostrar después que la
reparación funcionó. Anotar los números en la hoja de registro del final.

```bat
cd /d C:\pos_fifo_system
call deploy\env_cliente.bat
psql -U %DB_USER% -d %DB_NAME% -c "SELECT (SELECT count(*) FROM ventas_venta) AS ventas, (SELECT count(*) FROM cuentas_por_cobrar_cuentaporcobrar) AS cxc, (SELECT count(*) FROM clientes) AS clientes;"
psql -U %DB_USER% -d %DB_NAME% -c "SELECT estado, count(*) FROM sync_eventosync GROUP BY 1;"
```

Y la consulta que mide el daño silencioso — ventas que nunca encolaron su evento:

```bat
psql -U %DB_USER% -d %DB_NAME% -c "SELECT count(*) FROM ventas_venta v WHERE NOT EXISTS (SELECT 1 FROM sync_eventosync e WHERE e.tipo_evento = 'VENTA_CREADA' AND e.objeto_id_local = v.id);"
```

---

## 2. Copiar la carpeta `media` (ANTES de tocar la instalación)

Las imágenes de productos **no viajan en el dump de la base de datos**. Si el
cliente está en el cloud y sus fotos salen rotas en el portal, este es el único
momento en que se pueden recuperar sin volver a viajar.

```bat
xcopy /E /I C:\pos_fifo_system\media C:\temp\media_cliente
```

Llevarse esa carpeta. La subida a Blob se hace **después y en remoto**, siguiendo
`MIGRAR_IMAGENES_A_BLOB.md`. No hay que hacerla aquí.

**Verificar:** `dir C:\temp\media_cliente\productos` muestra archivos.

---

## 3. Actualizar

```bat
cd /d C:\pos_actualizacion
deploy\actualizar.bat
```

En orden: detiene los servicios, **respalda la BD**, copia el código nuevo
(respetando `media`, `logs`, `backups` y el `venv`), instala dependencias, migra,
recolecta estáticos, corre los seeds y diagnostica.

**Salida esperada:** cada fase en `[OK]` y, al final, el diagnóstico.

> **Convierte solo la configuración.** Si encuentra `env_cliente.bat` y todavía no
> existe `env_cliente.env`, lo migra al formato nuevo. Desde entonces, cambiar un
> valor es editar el `.env` y reiniciar el servicio — **nunca más re-registrar**.

---

## 4. Verificar la instalación

```bat
python manage.py verificar_instalacion --settings=config.settings_production
```

**Salida esperada:** `RESULTADO: instalacion sana.`

Este paso es el **gate**: no seguir con algo en rojo. Lo más importante que
detecta:

- **`DJANGO_SECRET_KEY` truncada** — la firma del bug #9.
- **Módulos vendibles apagados** — si aparece `impresion_termica`, **el POS no
  imprime tickets** aunque el flag diga que sí.

---

## 5. Medir el daño de sync

```bat
python manage.py verificar_sync --dias=120 --settings=config.settings_production
```

Reporta hechos sin evento, huecos de numeración, estado de la cola y cursores
bloqueados. **Anotar los números antes de reparar**: son el "antes" contra el que
se mide el resultado.

---

## 6. Reparar

Primero en seco, para ver qué haría:

```bat
python manage.py verificar_sync --dias=120 --backfill --reintentar-descartados --settings=config.settings_production
```

Y luego de verdad:

```bat
python manage.py verificar_sync --dias=120 --backfill --reintentar-descartados --ejecutar --settings=config.settings_production
```

- `--backfill` encola los eventos de hechos que nunca lo tuvieron.
- `--reintentar-descartados` devuelve a la cola los eventos que agotaron sus
  reintentos: ahí están las cuentas por cobrar que el cloud rechazaba.

**Reenviar de más es seguro:** el cloud deduplica por hash y cada handler corta
por clave natural. Dos envíos del mismo evento no duplican nada.

---

## 7. Empujar y confirmar

```bat
python manage.py sincronizar --once --settings=config.settings_production
```

**Salida esperada:** `PUSH procesados=N confirmados=N fallidos=0`.

Si `fallidos` no es 0, revisar el detalle antes de seguir.

```bat
deploy\nssm.exe restart POSFifoSystem
deploy\nssm.exe restart POSFifoSync
curl http://127.0.0.1:8080/api/v1/health/
```

---

## 8. Verificar en el portal

Entrar a `https://red-bay-07331a710.7.azurestaticapps.net` y confirmar:

- Las **cuentas por cobrar** aparecen con su titular real, no `CLIENTE CONTADO`.
- Los totales cuadran con lo que muestra el POS local.

> Las CxC que ya estaban en el cloud a nombre de `CLIENTE CONTADO` **se corrigen
> solas** con el reenvío: el handler rellena el titular cuando el payload nuevo
> permite identificarlo.

---

## 8b. Generar las miniaturas del catálogo

Solo hace falta una vez por instalación: de aquí en adelante cada foto que se
suba genera la suya sola.

```bat
python manage.py generar_miniaturas --apply --settings=config.settings_production
```

**Salida esperada:** `generadas: N`, `fallidas: 0`, y una línea de peso del
tipo `237.0 MB -> 1.5 MB`.

Sin este paso, la lista de productos y el punto de venta siguen cargando las
fotos originales —megabytes por cada cuadrito de 40 px— y el POS se siente
lento en las máquinas modestas. Un `fallidas` mayor que 0 apunta a fotos cuyo
archivo ya no está en `media\productos`; el producto sigue funcionando, solo
se muestra pesado.

---

## 9. Prueba de humo con el negocio

Con el dueño o la cajera presentes, antes de irse:

1. Registrar una venta de prueba y **confirmar que imprime el ticket**.
2. Anularla.
3. `verificar_sync` debe cerrar con `RESULTADO: sin perdida detectada.`

---

## Tabla de fallos

| Síntoma | Causa | Arreglo |
|---|---|---|
| El POS **no imprime** y no da error | La `ConfiguracionNegocio` no está ligada a la sucursal, así que `bootstrap_suscripciones` no derivó módulos. Firma: `negocio_modulos` con solo `cuentas_por_cobrar` | Ligar la config a la sucursal y re-ejecutar `bootstrap_suscripciones`. Ver BUG-D |
| La `SECRET_KEY` sale marcada como corta | Bug #9: un `&` la truncó en el formato `.bat` | Generar una nueva en el `.env`. **Invalida las sesiones abiertas** |
| Cambios en el `.env` que no toman efecto | El servicio conserva variables del formato viejo, y le ganan al archivo | Re-ejecutar `registrar_servicio.bat`; `nssm get POSFifoSystem AppEnvironmentExtra` debe mostrar **solo 2 variables** |
| `PUSH` con `Venta no existe en cloud todavia` | Orden de eventos de la versión vieja: la CxC salía antes que su venta | Se resuelve en el ciclo siguiente. Con esta versión ya no ocurre |
| Un cursor aparece **BLOQUEADO** | Un registro del portal falla al aplicarse y frena la marca de agua | El detalle dice cuál. Corregirlo en el portal y volver a sincronizar |
| El backup falla en `actualizar.bat` | Sin espacio, o `pg_dump` fuera del PATH | **Aborta a propósito.** Resolver antes de reintentar: sin backup no se migra |

---

## Hoja de registro de la visita

Llenar durante la visita y traer de vuelta. Esto es lo que mantiene vivo el
runbook.

```
Cliente:                     Fecha:            Ejecuto:
Version anterior:            Version nueva:

ANTES
  ventas ____  cxc ____  clientes ____  productos ____
  eventos por estado: _______________________________________
  ventas sin evento ____     huecos de numeracion ___________
  impresion termica activa:  SI / NO

REPARACION
  encolados por --backfill ____
  devueltos por --reintentar-descartados ____
  push: procesados ____  confirmados ____  fallidos ____

DESPUES
  cxc visibles en el portal ____   monto recuperado ____
  media copiada SI / NO   archivos ____   tamano ____

INCIDENCIAS Y SORPRESAS
  (lo que no coincidio con lo esperado: es la parte mas valiosa)
  ___________________________________________________________
  ___________________________________________________________
```

---

## Referencias

- Instalación desde cero: `INSTALACION_CLIENTE_NUEVO.md`
- Subir imágenes a Blob: `MIGRAR_IMAGENES_A_BLOB.md`
- Bugs citados: `../BUGS.md`
