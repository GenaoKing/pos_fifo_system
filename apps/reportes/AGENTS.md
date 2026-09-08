# apps/reportes — mapa para agentes

<!-- Última revisión: 2026-09-08 -->

> Mapa de orientación, no contrato. Apunta a código; la verdad del *cómo* está
> en los archivos enlazados. Si algo aquí no cuadra con el código, gana el código
> (y corregí este archivo en el mismo PR).

## Qué hace

Dashboard del POS local, reportes on-demand y reportes **persistidos**
(`models.py`): `CierreCaja` (`BORRADOR` se recalcula / `FINAL` congelado),
`TopProducto`, `InventarioValorizado`, más el PDF del cierre. El cloud **no**
usa esto: sus reportes son JSON en `apps/api/services/reporting.py`.

## Entrypoints (`app_name = 'reportes'`, montado en `/reportes/`; `/` redirige aquí)

| Necesito… | Voy a… |
| --- | --- |
| Dashboard | `views.dashboard`, `api_metricas_hoy` |
| On-demand | `views.reportes_on_demand`, `api_cierre_manual`, `api_ventas_periodo`, `api_top_productos`, `api_inventario_valorizado`, `api_ventas_cajero` |
| **Generar** un reporte | `report_manager.ReporteManager` → `generar_cierre_diario`, `generar_top_productos`, `generar_inventario_valorizado` |
| Cierre automático | `manage.py generar_cierre_diario` (`--tenant` / `--todos-los-tenants` / `--finalizar`) |
| PDF del cierre | `pdf_generator.PDFGenerator.generar_cierre_caja` → `views.descargar_pdf_cierre` |
| Dónde se guardan los PDFs | `almacenamiento.py` → `ruta_cierre` (`REPORTES_PRIVATE_ROOT`, **fuera** de `MEDIA_ROOT`) |
| Alcance | `scope.alcance_de` (`reportes.sucursal.ver` / `reportes.consolidado.ver`), `puede_ver_reportes`; `reportes.ver` para el dashboard |

## Invariantes / trampas

- La transacción se abre en el alias del tenant (`report_manager._atomic`,
  RPT-006); un borrador se recalcula (RPT-004); una fecha pasada se reconstruye
  desde el ledger de movimientos (RPT-002).
- Los PDFs financieros **nunca** en `MEDIA_ROOT` (se sirve sin login, RPT-001);
  `config/urls.py` además bloquea `media/reportes/`.
- Todo queryset se filtra con el **mismo** `Alcance` que declara RBAC (RPT-003).
- Encabeza con `config_para_documento(cierre.sucursal)` — ver `apps/common`.
- Auditoría 2026-08-20 (`docs/exploracion/AUDITORIA_CODIGO_APPS_REPORTES.md`) —
  **snapshot histórico**, verificar contra código.
