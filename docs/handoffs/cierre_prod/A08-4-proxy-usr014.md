# Handoff A08.4 - proxy ACA y USR-014

Estado: **código/documentación preparados; USR-014 permanece PENDIENTE de
preflight autorizado**. No se consultó un entorno Azure, no se cambió ingress,
variables, Terraform, base de datos ni tráfico real.

Commit de implementación: `fb92c73` (`fix(security): harden proxy IP
attribution`).

## Hallazgo corregido en código

La ruta de impersonación de portal tenía un extractor propio que tomaba la
primera entrada de `X-Forwarded-For`. Esa entrada puede ser suministrada por el
cliente. Ahora usa `apps.auditoria.models.get_client_ip`, que comparte la
política de auditoría:

- sin `AUDITORIA_CONFIAR_EN_PROXY`, usa `REMOTE_ADDR`;
- con proxy declarado, toma únicamente la última entrada de la cadena;
- `settings_cloud` expone la variable con default `False`, por lo que ningún
  despliegue empieza a confiar en proxy por accidente.

La documentación oficial de Azure Container Apps confirma que `X-Forwarded-For`
del cliente se conserva/agrega y que solo el extremo derecho es aportado por
ACA; `X-Forwarded-Proto` sí es sobrescrito. Django exige confiar en esos headers
solo cuando el proxy está bajo control. El runbook
`docs/runbooks/PROXY_USR014_A08.md` contiene enlaces oficiales y el plan de
preflight.

## Evidencia local

```powershell
python -m py_compile apps/api/auth_views.py `
  apps/api/tests/test_proxy_headers.py `
  config/settings_cloud.py `
  scripts/release/tests/test_proxy_usr014_policy.py
python -m unittest discover -s scripts/release/tests -v
git diff --check
```

La guarda estática verifica que impersonación no recupere un extractor propio,
que la confianza siga opt-in y que solo se lea el extremo derecho. El
`SimpleTestCase` Django en `apps/api/tests/test_proxy_headers.py` fija los casos
de runtime equivalentes para CI.

No se ejecutó ese `SimpleTestCase` localmente: este worktree no tiene un venv
con Django 5.2.17. El Python global no tiene Django y el conda disponible tiene
Django 5.0.8, que no es el runtime fijado. No se lo usó como sustituto.

## Criterio pendiente para cerrar USR-014

En una ventana autorizada se debe observar la cadena real de ingress, probar un
prefijo `X-Forwarded-For` suministrado por cliente, verificar el extremo derecho
y `X-Forwarded-Proto`, decidir la variable de proxy y comprobar una auditoría de
login/impersonación. Mientras eso no ocurra, `AUDITORIA_CONFIAR_EN_PROXY` queda
en `False` y USR-014 no puede declararse resuelto.
