# Runbook — Cierre diario manual (RPT-005)

**Decisión vigente (RPT-005 / DEC del cierre a producción):** el resumen diario
de ventas y cobros se genera **manualmente**. **No hay servicio de cierre
automático.** El launcher `instalar_cierre.ps1` — que registraba un servicio NSSM
`CierreCajaProgramado` apuntando a un `.bat` inexistente — fue **retirado**: un
servicio NSSM ejecuta un proceso de forma continua, no una tarea puntual a las
7 PM, así que reiniciaba en bucle un comando que además fallaba siempre. Si en
un runbook antiguo aparece `instalar_cierre.ps1`, ignoralo.

El comando que produce el cierre es idempotente respecto del día: mientras el
resumen sea BORRADOR se **recalcula** en cada corrida (RPT-004); solo
`--finalizar` lo congela (FINAL).

## Comando

Desde el entorno del POS (venv/conda activo, settings de la instalación):

```bash
# Resumen de HOY (BORRADOR: se recalcula en la próxima corrida)
python manage.py generar_cierre_diario

# Un día concreto
python manage.py generar_cierre_diario --fecha 2026-08-20

# Congelar el resumen (estado FINAL; ya no se recalcula solo)
python manage.py generar_cierre_diario --fecha 2026-08-20 --finalizar

# En el cloud (multi-tenant)
python manage.py generar_cierre_diario --tenant <tenant_key>
python manage.py generar_cierre_diario --todos-los-tenants
```

**Salida esperada** (ejemplo):

```
Generando resumen diario de 2026-08-20...
Resumen generado
  ID: 12
  Fecha: 2026-08-20
  Estado: BORRADOR (v3)
  Total Ventas: $45,300.00
  Cantidad Ventas: 87
  Turnos abiertos: 0
  PDF: reportes/cierres/2026/08/cierre_2026-08-20.pdf
```

- **Código de salida ≠ 0** solo si el cierre no se pudo calcular (el fallo queda
  auditado). El PDF es accesorio: si falla, el resumen igual se calcula y el
  comando avisa (`PDF no generado: ...`).
- Un `--todos-los-tenants` que falle en algún tenant termina en error nombrando
  los tenants fallidos, sin frenar a los demás.

## Si un cliente quiere una corrida programada

La decisión del release es cierre manual. Si una instalación quiere una corrida
diaria desatendida, se programa como **tarea del Programador de tareas de
Windows** (una acción puntual a una hora), **no** como servicio NSSM:

```
schtasks /Create /TN "POS Cierre Diario" /SC DAILY /ST 19:05 ^
  /TR "\"C:\pos_fifo_system\venv\Scripts\python.exe\" \"C:\pos_fifo_system\manage.py\" generar_cierre_diario"
```

Sin `--finalizar`: deja el día en BORRADOR y lo recalcula si hay ventas tardías;
el FINAL lo dispara una persona cuando cierra el día.

## Verificación

- `python manage.py generar_cierre_diario` produce/actualiza un `CierreCaja` del
  día en estado BORRADOR (probado en
  `apps/reportes/tests/test_comando_cierre_diario.py`).
- No existe ningún servicio `CierreCajaProgramado` registrado
  (`nssm status CierreCajaProgramado` → no encontrado / el servicio no debe
  existir).
