# Integración A04 + C05 parte 1

- Estado: **VALIDADO LOCALMENTE / LISTO EN `develop`**
- Fecha: **2026-09-11**
- Base común de ambos bloques: `develop@0b4fb7c`
- A04: `codex/cierre-prod-A04@0e4214d` (implementación `be15ea0`)
- C05: `claude/cierre-prod-C05@44e571e` (implementación `60c6dbc`)
- Árbol de código integrado y probado: `9ff61c2`

No hubo push, despliegue, Terraform, launchers, lectura de clientes ni
modificación de datos operativos.

## Revisión cruzada

Los dos bloques parten exactamente del mismo commit y sus archivos de código no
se solapan. C05 consume CT-02 en ventas/impresión y sigue emitiendo los eventos
de dominio existentes; A04 conserva esas firmas y cambia su transporte,
idempotencia y reconciliación. Los merges fueron limpios y no fue necesario
resolver conflictos manualmente.

La revisión de C05 comprobó:

- anulación autorizada bajo lock contra la sucursal de la propia venta;
- reimpresión/listados acotados por permiso y alcance;
- gate de módulo 404 en HTML y JSON para CxC/reportes on-demand;
- uso del permiso real `ventas.reimprimir`, sin un bypass de rol paralelo;
- preservación del fallback explícito para ventas legacy sin sucursal.

La comparación con A04 comprobó que esos cambios no alteran payloads ni firmas
de sync, y que el transporte nuevo sigue cubriendo el evento de anulación y el
snapshot post-commit. No se encontró un bloqueador de integración.

## Validación ejecutada sobre la combinación

Entorno: CPython 3.11.14, Django 5.2.17, PostgreSQL local, ejecución serial.
Se usó `DB_NAME=pos_cierre_integracion_a04_c05`; el runner creó y destruyó
`default` y tres BDs tenant con namespace `a04c05full`.

```text
manage.py check                              -> OK
makemigrations --check --dry-run             -> No changes detected
pip check                                    -> No broken requirements found
compileall (superficies A04/C05)              -> OK
git diff --check                             -> OK
matriz focal conjunta                        -> 237/237 OK (56.940 s)
suite Django completa                        -> 1.386/1.386 OK (486.442 s)
pytest apps/facturacion_electronica/tests -q  -> 72/72 passed (20.70 s)
```

Los warnings y respuestas 4xx del log pertenecen a escenarios negativos
intencionales. La suite terminó `OK` y destruyó las cuatro BDs de test.

## Base común siguiente

Después del commit documental de esta integración, `develop` es la única base
para A05 y para continuar C05. Cada agente debe iniciar una rama nueva desde ese
tip, conservar su worktree/BD/puertos aislados y no cherry-pickear de nuevo
`be15ea0` o `60c6dbc`.

Siguen fuera de este cierre: A05/CT-04, el resto financiero/operacional de C05,
la matriz HTTP real viejo/nuevo, el dry-run contra clientes y todos los gates
de staging/producción A08/A09/C06.
