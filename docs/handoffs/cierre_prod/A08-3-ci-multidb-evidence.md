# Handoff A08.3 - gate CI multi-BD y evidencia durable

Estado: **REVISION LOCAL**. Fecha: **2026-09-23**. Este bloque no ejecuta
GitHub Actions, Azure, Terraform, Docker ni una BD fuera del runner hipotético;
no crea ni elimina bases compartidas.

Commit de implementación: `43a9d9f` (`ci(release): require physical tenant
isolation gate`).

## Entrega

| Superficie | Cambio |
| --- | --- |
| `backend-ci.yml` | Paso explícito `Run physical DB-per-tenant isolation gate`: exige `TENANT_TEST_DB_NAMESPACE` y ejecuta TEN-016 antes de la suite general. Cubre `default` más dos BDs tenant físicas y la ruta de administración C04 entre control plane/tenant. |
| Evidencia CI | Los manifiestos de candidata y promoción se retienen 180 días. El workflow de reproducibilidad se activa además cuando cambia `backend-ci.yml`, por lo que un cambio del gate no puede esquivar su evidencia de locks, Docker y migraciones. |
| Regresión | Las guardas locales comprueban que el namespace no puede faltar, ambos módulos físicos siguen llamados y los dos workflows mantienen la retención/cobertura declaradas. |

El gate conserva los nombres físicos bajo un namespace de `run_id`/`attempt`;
no reutiliza aliases ni BDs tenant de otra corrida. La suite general sigue
corriendo después: este paso hace visible y obligatorio el control que antes
dependía solo de discovery.

## Evidencia local

```powershell
python -m py_compile scripts/release/tests/test_backend_ci_policy.py
python -m unittest discover -s scripts/release/tests -v
git diff --check
```

Resultado previo al commit: **8/8 tests OK** y diff sin errores. La ejecución
efectiva del gate requiere el servicio PostgreSQL efímero del runner GitHub; no
se simuló con una base local ni se acreditó como corrida remota.

## Decisiones de límite

- No se añadió `environment: prod` al job matricial. GitHub resolvería el
  Environment al iniciar cada fila, incluso las que finalmente calculan
  `deploy=false`; eso pediría approvals de prod en PRs/checks sin deploy. La
  preparación de approvals exige separar las filas deployables en jobs con
  elegibilidad previa.
- La retención de 180 días no reemplaza almacenamiento durable, firma ni
  atestación del manifiesto.
- El orden backend/portal no se puede imponer desde este repositorio sin un
  contrato/handshake del workflow del portal. Cualquier cambio allí queda para
  su dueño y su worktree, que en esta fecha está ocupado con C04 p5.3/E2E.

## Pendiente posterior

1. Ejecutar los workflows desde una rama publicada y conservar su evidencia.
2. Diseñar la separación de jobs/approval de GitHub Environments sin gatear
   checks no desplegables.
3. Acordar con frontend el handshake de orden y el manifiesto portal/Windows
   CT-05; no fusionar ambos extremos en una misma ventana.
4. Mantener los gates A08 restantes: proxy USR-014, preflight/migración real,
   restore drill y preparación autorizada de infraestructura.
