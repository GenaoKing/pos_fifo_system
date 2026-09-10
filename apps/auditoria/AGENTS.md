# apps/auditoria — mapa para agentes

<!-- Última revisión: 2026-09-10 -->

> Mapa de orientación, no contrato. Apunta a código; la verdad del *cómo* está
> en los archivos enlazados. Si algo aquí no cuadra con el código, gana el código
> (y corregí este archivo en el mismo PR).

## Qué hace

Registro **append-only** de acciones (`Auditoria`, `apps/auditoria/models.py`)
con snapshot del actor y hash de integridad por fila, middleware de captura
automática y un dashboard local en `/auditoria/`.

## Entrypoints (`app_name = 'auditoria'`)

| Necesito… | Voy a… |
| --- | --- |
| **Registrar una mutación nueva** | `services.registrar_mutacion(..., using=)` — contrato `audit.event.v1` / CT-01, redactado y transaccional |
| Adaptar un productor histórico | `Auditoria.registrar(...)`; solo compatibilidad durante migración |
| Cobertura real por tipo de evento | `productores.py` → `MATRIZ_V1` y `MATRIZ_LEGACY`; `SIN_PRODUCTOR` significa que el visor solo ofrece historia |
| Tipos de acción | `Auditoria.TipoAccion` (`TextChoices`) |
| Captura automática por request | `middleware.py` → `AuditoriaMiddleware`, `SesionAuditoriaMiddleware` |
| Dashboard / búsqueda | `views.py` → `dashboard_auditoria`, `api_auditoria_buscar` |
| Alcance por sucursal | `scope.py` → `alcance_de` (`auditoria.ver` / `auditoria.consolidado.ver`, sobre `apps.permisos.alcance`) |
| Verificar que nadie tocó la tabla por fuera | `manage.py verificar_auditoria` (hash por fila + huecos de `id`) |
| Retención | `AuditoriaQuerySet.purgar_hasta(...)` — la **única** vía de borrado |

## Invariantes / trampas

- `update()` / `delete()` lanzan `AuditoriaInmutable`. `save()` congela el actor
  (`actor_*`) y calcula el hash; los registros previos a `0005` quedan sin hash.
- Agregar una acción = nuevo miembro en `TipoAccion` + migración (solo
  `choices`), p. ej. `auditoria.0007` (`COMPROBANTE_PDF`).
- Una mutación exitosa nueva llama `registrar_mutacion` dentro del mismo
  `transaction.atomic(using=...)`; un fallo del evento revierte el dominio. Los
  intentos fallidos se registran después del rollback. Logout es la excepción:
  invalida primero la sesión y su log es best-effort.
- El adaptador `Auditoria.registrar` sigue disponible para productores legacy,
  pero no satisface por sí solo CT-01.
- CT-01 persiste referencias opacas separadas para el actor operativo y, cuando
  corresponde, la identidad global que impersona. No guardar credenciales o
  tokens en snapshots, metadata ni errores.
- `Auditoria.derivar_sucursal(objeto)` para inferir la sucursal de un hecho.
- Auditoría 2026-08-20 (`docs/exploracion/AUDITORIA_CODIGO_APPS_AUDITORIA.md`) —
  **snapshot histórico**, verificar contra código.
