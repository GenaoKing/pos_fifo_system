# A07 — reconciliación del inventario y evidencia backend

Fecha: **2026-09-18**. Estado: **completado localmente; no es autorización de
release**.

## Entrada, aceptación y límites

El responsable aceptó CT-03 sync y C04 p6 antes de abrir A07. La base backend
revisada fue `integration/cierre-prod-A06-C04-C05@1357cd7`; las puntas aceptadas
siguen siendo el merge CT-03
`6c74d163b41d32a3e3a94ec15c19b90260d6bcde` y el frontend C04
`claude/cierre-prod-C04@f0e6c2d0ad579b43ec5d445a3ff4f1e43da6ebd1`.

Este bloque no movió `develop`, no hizo push, no publicó, no desplegó ni leyó o
escribió datos de clientes. A08, C06, `OPS-PRO-007` contra una instalación real,
el preflight de RBAC/ADMIN y cualquier reparación siguen fuera de alcance.

## Evidencia reproducida

En el worktree aislado `pos_fifo_system_a07_inventario`, con el entorno
`C:\Proyectos\pos_fifo_system_a06\.venv` y bases descartables:

```powershell
$env:DB_NAME = 'pos_fifo_a07_inventario'
$env:TENANT_TEST_DB_NAMESPACE = 'a07_inventario_20260918'
$apps = @('auditoria', 'usuarios', 'negocios', 'permisos', 'tenancy', 'sync', 'productos', 'clientes')
$tests = foreach ($app in $apps) {
  rg --files "apps/$app/tests" -g 'test_*.py' |
    ForEach-Object { ($_ -replace '[\\/]', '.') -replace '\.py$', '' }
}
& 'C:\Proyectos\pos_fifo_system_a06\.venv\Scripts\python.exe' manage.py test @tests --settings=config.settings_development --noinput --verbosity 1
```

Resultado: **605 tests, 165.049 s, OK**. El runner creó y destruyó `default` y
dos bases tenant namespaced de TEN-016. Los `403`, `400`, `409` y excepciones
registradas durante los casos adversariales son aserciones esperadas, no fallos
del runner.

La primera pasada encontró cuatro errores 403 en
`apps.clientes.tests.test_auditoria_clientes`: los casos que intencionalmente
simulan la instancia cloud no declaraban
`API_MAESTROS_PERMITE_ESCRITURA_LOCAL_TEST=True`. Se añadió
`@override_settings` únicamente a esas dos clases. La prueba focal volvió verde
**24 tests, 10.600 s**, y después la matriz completa quedó verde. El POS real
sigue cubierto por el caso que recibe 403 sin ese flag.

## Resultado de la reconciliación

| Familia | Resultado A07 | Evidencia / límite |
| --- | --- | --- |
| AUD, USR, NEG, PER | Revalidadas localmente | La matriz cubre los cierres previos. USR-014 conserva A08 porque depende de proxies reales. |
| TEN | Revalidada localmente | TEN-016 ejerció dos bases físicas aisladas; esto no sustituye la topología real de producción. |
| sync | Revalidada localmente | CT-03 SUS-007/CFG-007, ACK, cursor, esquema inválido y diferidos siguen fail-closed. |
| PRO | Cuatro discrepancias del ledger cerradas | PRO-002, PRO-003, PRO-004 y PRO-019 pasan a `ACREDITADO_LOCAL`; sus remanentes no se declaran cerrados sin la prueba indicada. |
| CLI | Contención confirmada y deuda clasificada | CLI-004 queda `DIFERIDO_EXPLICITO`: no hay cola offline/CAS de clientes. Los demás pendientes mantienen su criterio de cierre, sin prometer que una prueba de otro dominio los cubra. |

La decisión de escritor para Producto/Categoría ya está implementada: mutación
local durable, auditada, idempotente y con CAS al cloud. No se extrapoló esa
conclusión al cliente: una edición local de cliente ya adoptado devuelve 409,
por lo que evita pérdida silenciosa pero no entrega edición offline.

## Deuda que sigue visible

| Grupo | Estado y siguiente evidencia |
| --- | --- |
| PRO-009–017, PRO-020–022 | Pendientes de sus pruebas UI/modelo, auditoría portal, concurrencia, ciclo de imágenes, contrato de errores, impresión y administración. No fueron absorbidos por un test verde de transporte. |
| CLI-006, CLI-008–013, CLI-015–021 | Pendientes de aislamiento específico de cliente, `full_clean`, canon de documento, ciclo de vida, CT-01 por mutación, sucursal, detalle, presupuesto de queries, admin y contrato CRUD. CLI-013 se reconfirmó: el API aún borra físicamente un cliente real. |
| USR-014, PER-ADMIN-BYPASS, OPS-* | Operativos/A08: requieren configuración o datos reales autorizados. |
| Productores CT-01 no incluidos en C05 p6 | Edición de venta, edición de compra y conversión de cotización por venta conservan dueño y handoff propios. |

No hay un hook backend nuevo pendiente para C04 p5. El frontend debe consumir los
contratos de maestros, RBAC y configuración ya integrados; cualquier carencia de
contrato debe volver primero a Codex como solicitud explícita, no resolverse con
un mock o una mutación backend desde el repositorio React.

## Siguiente integración

El siguiente trabajo permitido de Claude es **C04 p5**, en un worktree frontend
nuevo desde `claude/cierre-prod-C04@f0e6c2d0ad579b43ec5d445a3ff4f1e43da6ebd1`:

1. Pantallas de roles/asignaciones, configuración y operaciones alternativas al
   `/admin/` cloud, sin modificar endpoints backend ni duplicar RBAC en React.
2. Conexión al backend integrado, incluidos 401/403, alcance por sucursal,
   revocación y errores parciales; mantener los selectores/paginación C04 p6.
3. Entregar SHA, build, lint y tests. Si añade E2E real, que sea un bloque
   explícito: hoy no hay Playwright ni Cypress y no se acreditó click-through de
   navegador.

Al finalizar C04 p5 se revisa en su repositorio y se vuelve a cruzar únicamente
lo afectado. Ningún resultado de ese bloque habilita `develop`, `main`, Azure ni
una instalación POS sin los gates A08/A09 y autorización expresa.
