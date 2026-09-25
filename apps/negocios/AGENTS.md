# apps/negocios — mapa para agentes

<!-- Última revisión: 2026-09-10 -->

> Mapa de orientación, no contrato. Apunta a código; la verdad del *cómo* está
> en los archivos enlazados. Si algo aquí no cuadra con el código, gana el código
> (y corregí este archivo en el mismo PR).

## Qué hace

`Negocio` (`apps/negocios/models.py`) es el tenant **lógico**: agrupa
sucursales y ancla roles/permisos (`apps/permisos`) y entitlements
(`apps/suscripciones`). Bajo DB-per-tenant cada base tenant tiene su fila
(`Negocio.self_row()`); el tenant **físico** es `apps/tenancy`.

## Entrypoints

| Necesito… | Voy a… |
| --- | --- |
| **Resolver el tenant de un request** | `utils.resolver_negocio(request)` → `Resolucion` (`.tenant(negocio)` / `.global_()` / `.sin_acceso(motivo)`); `.filtrar(qs, campo='negocio')` |
| Negocio o `None` | `utils.negocio_actual(request)` |
| ¿Es operador global? | `utils.es_principal_global(user)` |
| Crear negocio lógico | `services.crear_negocio(...)`; slug con reintento acotado y CT-01 |
| Actualizar/lifecycle | `services.actualizar_negocio(...)`; lock, motivo obligatorio y CT-01 |

## Invariantes / trampas

- `None` **ya no** significa "sin filtro" (NEG-001/002): un fallo de resolución
  es `sin_acceso`, y `?negocio=` inexistente es 404, no "todos". Usar siempre
  `Resolucion`; no leer `request.user.negocio` suelto.
- `NegocioAmbiguo` si una base tenant tiene más de un `Negocio`.
- `Negocio` no lleva logo/dirección/teléfono: eso es `ConfiguracionNegocio`
  (por sucursal).
- `slug` es identidad inmutable después del alta. `rnc_canonico`, cuando existe,
  contiene exactamente nueve dígitos y es único; las migraciones abortan ante
  colisiones, no inventan ni fusionan identidades.
- `Usuario.negocio` es `PROTECT` (`usuarios.0004`): borrar un negocio con
  usuarios falla. App **dual-home** en el router de tenancy.
- Auditoría 2026-08-20 (`docs/exploracion/AUDITORIA_CODIGO_APPS_NEGOCIOS.md`) —
  **snapshot histórico**, verificar contra código.
