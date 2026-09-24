# Integración local — C04 p5.2 administración del portal

Fecha: **2026-09-23**. Estado: **INTEGRADO y revalidado localmente; no
publicado, no desplegado.**

## Resultado y referencias exactas

| Superficie | Base / fuente | Resultado |
| --- | --- | --- |
| Backend de contrato | `integration/cierre-prod-A06-C04-C05@4fd5c4665784be7ef52f4f6bd7f8cf5ef39f7426` | Sin cambios en esta integración; administración tenant-scoped, CT-01, RBAC global y baja lógica ya acreditados. |
| Frontend base | `claude/cierre-prod-C04@2df99c2407fd90c457b178f0f8d5bb65ba054fda` | Incluye integración previa de C04 p6. |
| Frontend fuente | `claude/cierre-prod-C04-p5.2@e8b0a174482df450eaabbd4f5c108aa24424ea14` | Pantallas de usuarios, sucursales y configuración contra el contrato real. |
| Frontend integrado | `integration/cierre-prod-C04-admin@26e9bac8407e4cf8c3f5f36c8971a86a9093698c` | Merge `--no-ff` de p5.2 sobre C04+p6. |

La rama alternativa `claude/cierre-prod-C04-p5-admin@e18b562` **no** se
fusionó: parte de la misma base y se solapa en `lib/errors.ts` y
`AdminQueryError.tsx`; p5.2 es el superset que además cubre 404.

`develop@fffd02b`, `main`, las ramas fuente de Claude y el backend quedaron
inmóviles. No se hizo push, despliegue, migración ni acceso a datos operativos.

## Superficie integrada

- `/usuarios`: alta, edición acotada y baja lógica con motivo; no expone edición
  de `username` ni `email`.
- `/sucursales`: alta, edición acotada y baja lógica con motivo; el código solo
  se establece al crear.
- `/configuracion`: consulta y edición por sucursal de la allowlist que devuelve
  el backend; manda solamente los campos modificados y motivo.
- Conserva las rutas y cobertura de conflictos p6. Las rutas y accesos usan los
  gates de UI como defensa en profundidad, pero la autorización global continúa
  siendo exclusiva del backend.

## Validación distinguida por origen

### Repetida sobre el merge `26e9bac`

Se ejecutó en el worktree aislado
`C:/Proyectos/pos_cloud_dashboard_c04_integration`:

```powershell
npm ci
npm run lint       # OK
npm run test:run   # 19 archivos, 132/132 OK
npm run build      # tsc -b + vite build OK
```

`npm ci` informó 16 vulnerabilidades transitivas (2 low, 5 moderate, 9 high).
No se ejecutó `npm audit fix`: cambiar dependencias no pertenece a esta
integración y requiere un bloque de actualización/revisión propio.

### Evidencia heredada, no repetida aquí

El handoff de p5.2 acredita un smoke **20/20** contra
`integration/cierre-prod-A06-C04-C05@4fd5c46`, con dos negocios y sesión+CSRF
en una BD desechable. Cubre altas, inmutabilidad, baja idempotente, 403 de
cajera, 404 cross-tenant y allowlist de configuración. El backend acredita por
separado 58 pruebas CT-01/tenancy/API y un gate físico de dos PostgreSQL con JWT
tenant-aware y revocación de Membership.

## Límites que permanecen

1. No hubo E2E real de navegador: la UI se validó con Vitest/jsdom y el contrato
   HTTP se acreditó por smoke separado. No se instaló Playwright en este bloque.
2. El cliente React no ejerció todavía su flujo JWT tenant-aware contra el
   backend multi-DB; no confundir el gate backend con una prueba del navegador.
3. No forman parte de p5.2 cambio de contraseña, logo, emisor e-CF, módulos ni
   otros campos fuera de la allowlist. Tampoco cierra los preflights por tenant,
   C06, CT-05 ni los gates G1-G4.

## Reparto siguiente permitido

Los dos bloques siguientes no comparten checkout, archivos ni bases de prueba:

| Agente | Bloque | Base y límite | PASS mínimo |
| --- | --- | --- | --- |
| Claude | **C04 p5.3 E2E** | Nuevo worktree frontend desde `integration/cierre-prod-C04-admin@26e9bac`; backend desechable en `4fd5c46`, puerto/BD/tenants propios. Playwright puede agregarse ahora. Sin mocks, sin editar backend, sin `main`/push/despliegue. | Login JWT real y flujos de p6 + usuarios/sucursales/configuración, incluidos 403/404 y un caso cross-tenant; lint, Vitest, build y E2E verdes; handoff con datos/BDs destruidos. |
| Codex | **A08.1 reproducibilidad de release** | Nuevo worktree backend desde `integration/cierre-prod-A06-C04-C05@4fd5c46`. Solo CI, locks, Docker/artefacto, manifests y validaciones aisladas; no Terraform apply, no staging, no tenants reales ni cambio de `develop`. | Build y checks reproducibles sin secretos, inventario de migraciones por control-plane/tenant y plan de restore/rollback verificable en laboratorio; handoff con evidencia y cualquier límite de C06. |

## Gates bloqueados por deuda o autorización

- **C06 / CT-05:** falta ensayo de paquete, actualización Windows, rollback e
  impresión en laboratorio con el artefacto de A08; no se puede cerrar G1.
- **Preflights operativos:** retiro de `RBAC_LEGACY_ADMIN_BYPASS`, asignación
  real de `configuracion.administrar`, `OPS-PRO-007`, migraciones y reparación
  BUG-K requieren autorización explícita y datos reales. Permanecen bloqueados.
- **G2-G4:** staging, producción cloud y rollout RP/SK esperan G1, acta y
  autorización operacional. Esta integración no autoriza ninguno.
- **Deuda funcional fuera de p5.2:** password/perfil, campos de configuración
  excluidos y pendientes P2/P3 del inventario no se consideran cerrados por las
  nuevas pantallas.

## Reversión local

No hay cambios en ramas compartidas. Para abandonar este candidato basta con
volver a `claude/cierre-prod-C04@2df99c2` como base de frontend y conservar
`integration/cierre-prod-A06-C04-C05@4fd5c46` como backend de referencia. No
se requiere reversión de esquema ni de datos.
