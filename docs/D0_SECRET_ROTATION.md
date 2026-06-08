# D0 - Rotacion de secretos expuestos

Este documento enumera secretos que deben rotarse fuera del repo despues de
sanear los archivos versionados. No guardar aqui valores reales.

## Rotacion obligatoria

- Token de sucursal usado por `CLOUD_API_TOKEN` en scripts de sync.
- Credenciales MSeller TesteCF usadas por `MSELLER_EMAIL_ROYAL`,
  `MSELLER_PASSWORD_ROYAL` y `MSELLER_API_KEY_ROYAL`.
- Passwords de Azure PostgreSQL o Azure SQL que hayan estado en archivos locales
  o compartidos durante pruebas.
- Password inicial de usuarios SYSADMIN creados con instaladores anteriores.
- Cualquier `DJANGO_SECRET_KEY` que haya sido pegada en scripts, docs, chats o
  capturas.

## Archivos locales ignorados

Los archivos `deploy/env_*_local.bat` estan ignorados por git, pero pueden
contener secretos reales en la maquina de desarrollo. Si fueron compartidos o
mostrados, rotar sus credenciales en el proveedor correspondiente y volver a
llenarlos localmente.

## Regla para D1+

Docker, Terraform y GitHub Actions deben recibir secretos desde variables de
entorno, Container Apps secrets, Key Vault o el mecanismo seguro equivalente.
No usar `.bat`, docs ni settings versionados para valores reales.
