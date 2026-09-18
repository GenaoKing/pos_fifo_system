# A / C04 p5.2 — contrato backend de administración del tenant

Fecha: **2026-09-18**. Estado inicial: **contrato y transferencia de
responsabilidad aceptados para implementación local; no autoriza publicar ni
desplegar**.

## Motivo y frontera

C04 p5 no debe inventar mutaciones desde React ni usar `/admin/` cloud. El
relevamiento posterior a A07 confirmó que los selectores RBAC existentes son
de solo lectura y faltan tres superficies de escritura para que Claude pueda
conectar sus pantallas p5.2:

1. ciclo de vida de usuarios del tenant;
2. ciclo de vida de sucursales;
3. configuración operativa por sucursal.

Por petición expresa del responsable, **Codex/A** toma este bloque backend
acotado. Claude/C04 conserva el repositorio frontend y no implementa mocks ni
mutaciones backend. Playwright sigue diferido: primero se acreditan los
contratos HTTP reales.

## Transferencia acotada de propiedad

Durante este bloque, A puede modificar únicamente:

- `apps/api/views/administracion.py`, sus serializers, rutas y pruebas;
- servicios nuevos o extensiones estrictamente necesarias en
  `apps/usuarios`, `apps/sucursales` y `apps/configuracion` para que esas
  mutaciones sean transaccionales, RBAC y CT-01;
- este handoff, `CONTRATOS.md` y los índices de estado que registren el
  resultado.

No transfiere a A el modelo/admin/POS de configuración, entitlements de
suscripción, plantillas ni el frontend. Claude retoma el consumo C04 p5.2
solamente desde el SHA backend que este documento marque como aceptable.

## Contrato propuesto (v1, antes de implementar)

Todas las rutas viven bajo `/api/v1/administracion/`, son JWT de portal y
resuelven el tenant con `resolver_negocio()`. Un principal sin tenant explícito
no obtiene un queryset global por accidente. Los controles de topología e
identidad requieren el permiso global `permisos.administrar`; configuración
requiere `configuracion.administrar`. Un rol acotado a una sucursal no puede
alterar identidad, topología ni flags transversales del negocio.

| Recurso | Ruta y métodos | Semántica segura |
| --- | --- | --- |
| Usuarios | `usuarios/` GET, POST; `usuarios/{id}/` GET, PATCH, DELETE | POST crea `Usuario` y, con tenancy activa, su `Identity` y `Membership` del tenant. PATCH no cambia username/email/password; `activo=false` y DELETE son baja lógica. Ambos desactivan Usuario y revocan Membership, por lo que el JWT deja de autorizarse en el siguiente request. Nunca hay DELETE físico. |
| Sucursales | `sucursales/` GET, POST; `sucursales/{id}/` GET, PATCH, DELETE | CRUD lógico dentro del negocio. DELETE es desactivación (`activa=false`), no borrado de una sucursal que pueda tener ventas, configuración o eventos. |
| Configuración | `configuraciones/` GET; `configuraciones/{id}/` GET, PATCH | No se crea implícitamente configuración ni se elimina. PATCH permite solo identidad/contacto, operación/caja, pagos, descuentos e ITBIS; deja fuera logo, emisor e-CF y `modulo_*`, que tienen flujos y entitlements propios. |

Las respuestas no incluyen contraseñas, hashes, API keys ni secretos. Las
mutaciones persisten CT-01 con canal `PORTAL_API`, actor, tenant y sucursal
cuando corresponde. Los errores de validación son 400, objetos fuera de tenant
404 y falta de permiso 403. La baja debe ser idempotente.

## Identidad y consistencia entre bases

Cloud separa la base de tenant de `Identity`/`Membership` del control plane.
No existe una transacción distribuida Django entre ambas. El servicio debe:

- prevalidar scope, actor, email y username antes de escribir;
- compensar una alta incompleta dejando el acceso revocado, nunca una cuenta
  activa sin vínculo portal;
- registrar el límite si una caída de infraestructura impide confirmar ambos
  lados; no declarar una atomicidad que la topología no ofrece.

Una baja de un usuario de un tenant revoca su **Membership**, no desactiva a
ciegas una Identity global que en el futuro podría pertenecer a otro tenant.

## Criterio de salida para C04 p5.2

A entrega SHA, contrato final y pruebas focales que cubran aislamiento de
tenant, 401/403, alta, baja/revocación, no borrado físico, CT-01, allowlist de
configuración y errores de validación. Recién entonces Claude conecta las
pantallas con esos endpoints; no hay autorización para mover `develop`, push,
publicar, desplegar ni operar datos reales.
