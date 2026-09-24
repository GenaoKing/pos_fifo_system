# C06 — diagnóstico de encoding térmico en laboratorio

Estado: **EN CURSO**. Fecha: **2026-09-23**. Base: candidato local
`integration/cierre-prod-A06-C04-C05@9e6fb99`. Sin push, despliegue, servicios
Windows, migraciones ni lectura/escritura de datos operativos.

## Transferencia puntual de propiedad

`utils/impresoras/**` es propiedad del agente B / Claude. El responsable del
producto autorizó explícitamente a Codex a continuar el ensayo físico y a
corregir el diagnóstico que lo bloqueaba. Para este bloque, la transferencia es
deliberadamente mínima: `utils/impresoras/termica.py` y pruebas nuevas bajo
`utils/impresoras/tests/**`. No transfiere el resto de C02/C06, el paquete
Windows, instalaciones, servicios NSSM, tickets de venta ni reimpresiones.

## Hallazgo y evidencia física inicial

La página `ThermalPrinter2Connect.print_test_page()` declaraba validar
caracteres especiales, pero enviaba texto ASCII (`n N a e i o u` y `? ! $`).
Por tanto no podía detectar un fallo de encoding.

En la impresora local de laboratorio `POS-80C` (Windows, puerto `USB002`, estado
normal) se enviaron, mediante un trabajo RAW aislado y sin acceso a Django/BD ni
pulso de cajón, tres filas con caracteres reales `ñ Ñ á é í ó ú ü ¿ ¡`:

| Tabla ESC/POS | Resultado informado por el responsable |
| --- | --- |
| CP850 (`ESC t 2`) | PASS |
| CP1252 (`ESC t 16`) | PASS |
| CP858 (`ESC t 19`) | No se interpreta; no se selecciona |

Se conserva **CP850**, que coincide con el default de `THERMAL_CHARSET` y
`THERMAL_PRINTER[CODE_PAGE]`. La siguiente prueba física deberá ejecutar la
página de prueba de la aplicación ya corregida, no el emisor RAW de diagnóstico.

## Criterio de cierre de este sub-bloque

1. La página de prueba imprime los caracteres reales y muestra el code page
   configurado, sin hardcodear CP850.
2. Pruebas unitarias cubren el contenido y que la conexión selecciona la tabla
   configurada.
3. `POS-80C` confirma físicamente la página CP850 corregida, con corte y sin
   apertura de cajón.

Esto solo acredita el encoding y corte de la página diagnóstica. Continúan
pendientes la matriz C06 completa (ticket, etiqueta, comprobante, reimpresión,
nombres largos, imágenes, importes límite, cuenta de servicio), paquete
Windows, backup/restauración y rollback.

## Implementación y pruebas automatizadas

Se corrigió la página diagnóstica para que use el code page configurado e
imprima `ñ Ñ á é í ó ú ü ¿ ¡` reales. La conexión Windows abre/cierra el trabajo
RAW de modo explícito, inicializa con `ESC @` y selecciona la tabla configurada;
no envía comandos propietarios de un modelo concreto. El cierre no agrega
comandos de formato después del corte, pues cada nuevo trabajo ya se inicializa.

También se corrigieron dos hallazgos del ticket: los totales se identifican como
`RD$` y los nombres de producto de más de 40 caracteres se parten en líneas, en
vez de truncarse silenciosamente.

```
python manage.py test utils.impresoras.tests.test_termica \
  --settings=config.settings_development --verbosity 2
# 5/5 OK; Skipping setup of unused database(s): default.

python -m compileall -q utils/impresoras
git diff --check
# OK
```

Las pruebas cubren selector CP850/CP1252, página con caracteres reales, cierre
del trabajo RAW, wrapping sin pérdida y símbolo RD$.

## Ensayo físico adicional y estado del laboratorio

Una página de aplicación CP850 corregida y un ticket sintético se enviaron con
`POS-80C`; ambos devolvieron éxito y dejaron la cola Windows vacía. El ticket
no creó venta, pago ni auditoría: QR, logo y cajón estuvieron deshabilitados e
incluyó nombre largo con acentos, descuento y totales RD$.

Durante el ensayo, los trabajos 7--10 y una prueba mínima quedaron con
`PagesPrinted=0` aunque Windows mostraba estado normal. La causa fue externa al
código: la impresora se había conectado a otro puerto USB físico y el driver
`POS-80C` estaba asociado a `USB002`. Al volver al puerto donde se configuró el
driver, la cola se liberó. Los trabajos sintéticos no impresos se cancelaron
para evitar una emisión tardía duplicada; no contenían datos operativos.

La aceptación visual del ticket enviado sigue pendiente del responsable. No se
declara cerrada la matriz física C06 hasta contar con esa confirmación y los
casos que requieren una instalación/cuenta de servicio o la impresora de
etiquetas.

## Validación automatizada

Ejecutado con el venv aislado de C06.1 (Django 5.2.17) y el `.env` sintético
del laboratorio, sin crear ni consultar una BD:

```powershell
$env:POS_ENV_FILE = 'C:\Proyectos\_lab_pos_c06_1_20260923\env_lab\env_cliente.env'
C:\Proyectos\.venvs\pos_c06_1_install_check_20260923\Scripts\python.exe `
  manage.py test utils.impresoras.tests.test_termica `
  --settings=config.settings_development --verbosity 2
```

Resultado: **2/2 PASS**, sin BDs usadas. Cubre los caracteres latinos reales,
la etiqueta del code page configurado, el corte y la selección de `CP1252` por
la conexión Windows sin depender de una impresora física.
