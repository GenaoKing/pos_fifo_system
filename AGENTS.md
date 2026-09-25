# AGENTS.md — Instrucciones para agentes en pos_fifo_system

Este archivo orienta a cualquier agente de IA (Codex, Claude Code u otro) que
trabaje en este repositorio. No duplica conocimiento: apunta a la fuente de
verdad, que vive versionada en el repo. Léelo antes de tocar nada.

## Orden de lectura obligatorio

1. **[CLAUDE.md](CLAUDE.md)** — guía compartida del proyecto: stack, estructura,
   convenciones de tests, contratos de API, patrones de ViewSet y sincronización.
   Es la referencia base para todos los agentes, no solo Claude.

2. **[docs/PROJECT_STATUS.md](docs/PROJECT_STATUS.md)** — índice del estado del
   proyecto. Úsalo como punto de entrada, y **verifica las fechas antes de
   actuar**: si un dato parece viejo, confírmalo contra el código o `git log`
   antes de basar una decisión en él.

3. **El runbook correspondiente**, antes de cualquier operación sobre una
   instalación, el sync, imágenes o infraestructura. Están en
   [docs/runbooks/](docs/runbooks/) y son procedimientos deterministas paso a
   paso. No improvises un procedimiento que ya tiene runbook.

4. **[docs/ESTADO_AUDITORIAS.md](docs/ESTADO_AUDITORIAS.md)** — léelo siempre que
   trabajes en auditorías de código (migraciones pendientes de desplegar,
   permisos nuevos, cambios de contrato, decisiones abiertas y alcance).

## Antes de modificar

- **Revisa el estado de Git** (`git status`, `git log`) antes de editar.
  Preserva cambios concurrentes: no descartes ni pises trabajo sin commitear que
  no sea tuyo. Ante duda, detente y reporta en vez de sobrescribir.
- El repositorio es la memoria compartida entre agentes: lo que una IA necesita
  que otra sepa debe quedar escrito en un archivo versionado y en el mensaje de
  commit, no solo en un chat.
