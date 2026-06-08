# Terraform — Modelo mental (primer)

Tutorial conceptual antes de escribir el `infra/azure/` real. Escrito para alguien
que ya maneja infra on-prem; el foco está en lo que es **propio de Terraform/IaC**,
no en explicar qué es una VM o un balanceador.

Cuando pasemos a código (D2), los `.tf` van comentados igual de denso; esto es el
"por qué" para que esos comentarios tengan dónde anclarse.

---

## 1. El cambio de paradigma: declarativo, no imperativo

Un script `az cli`/`bash` es **imperativo**: una secuencia de pasos ("creá el RG,
después el ACR, después…"). Si lo corrés dos veces, falla o duplica. Vos te
encargás del orden, de los reintentos y de "¿esto ya existe?".

Terraform es **declarativo**: describís el **estado final deseado** y él calcula
qué hay que crear/cambiar/destruir para llegar ahí. Correrlo dos veces sin cambios
= no hace nada (idempotente).

> Analogía on-prem: es más parecido a un manifiesto de Kubernetes o a Puppet
> (estado deseado que se reconcilia) que a un runbook de pasos. No es Ansible
> "tarea por tarea"; es "así debe quedar el mundo".

---

## 2. El concepto central: los tres mundos

Todo Terraform se entiende si tenés claros **tres mundos** y cómo se comparan:

```text
   CONFIG (.tf)            STATE (.tfstate)          REAL (Azure)
   lo que querés     ⇄     lo que TF cree       ⇄    lo que existe
                            que existe                de verdad
```

- **Config**: tus archivos `.tf`. La intención.
- **State**: un JSON donde Terraform guarda el mapeo "este recurso de mi config =
  este recurso real con este ID en Azure", más metadata.
- **Real**: lo que Azure realmente tiene ahora mismo.

`terraform plan` = **el diff entre estos tres**. Compara config vs state vs real y
te dice: "voy a crear esto, cambiar aquello, destruir esto otro". Nada se toca
hasta el `apply`.

**Drift** = cuando *real* se desvía de *state* sin pasar por Terraform (alguien
tocó el portal de Azure a mano). El próximo `plan` lo detecta y propone "corregir"
hacia tu config. Por eso ClickOps + IaC sobre el mismo recurso pelean.

> Analogía: el **state es tu CMDB/inventario fuente de verdad**. `plan` es un
> change request en modo dry-run. Drift es "alguien cambió el server sin ticket".

---

## 3. State — el concepto que más muerde a principiantes

El state existe por tres razones:

1. **Mapeo identidad lógica → recurso real.** En tu config el RG se llama
   `azurerm_resource_group.main`; en Azure tiene un Resource ID largo. El state
   guarda esa equivalencia. Sin él, Terraform no sabría que "ese RG de Azure" es
   "este de mi config".
2. **Performance.** Cachea atributos para no consultar toda la API en cada `plan`
   (aunque puede refrescar con `-refresh`).
3. **Metadata y dependencias** para ordenar y para detectar destrucciones.

Cosas que tenés que saber sí o sí:

- **El state contiene secretos en texto plano** (passwords de DB, claves que
  generes). Por eso: nunca commitearlo, y en cuanto deje de ser un juguete local,
  moverlo a un **backend remoto cifrado** (Azure Storage) con **locking** (para que
  dos `apply` simultáneos no lo corrompan).
- **Local primero (aprendizaje), remoto antes de compartir/prod.** Local =
  `terraform.tfstate` en tu disco. Remoto = bloque `backend "azurerm"` apuntando a
  un Storage Account.
- **Un state por ambiente.** dev, staging y prod tienen states separados. Nunca un
  solo state gigante para todo. (Por eso la estructura `environments/dev|staging|prod`.)

```text
.tfstate          -> NO se commitea (gitignore). Tiene secretos.
.terraform.lock.hcl -> SÍ se commitea. Fija versiones de providers (reproducible).
.terraform/       -> NO se commitea. Cache local de providers/módulos.
```

---

## 4. Providers — el "driver" hacia la API

Terraform por sí solo no sabe nada de Azure. Un **provider** es el plugin que
traduce tu HCL a llamadas concretas de API. Para vos: `azurerm` (habla con Azure
Resource Manager).

```hcl
terraform {
  required_providers {
    azurerm = { source = "hashicorp/azurerm", version = "~> 4.0" }
  }
}

provider "azurerm" {
  features {}   # bloque obligatorio del provider azurerm (aunque vaya vacío)
}
```

- **Autenticación**: en local, `az login` (Terraform reusa tu sesión de Azure CLI).
  En CI, un Service Principal o, mejor, **OIDC** (sin secretos de larga vida).
- **Versión fijada** (`~> 4.0`) + el `.terraform.lock.hcl` = builds reproducibles.
  Sin fijar, un `init` futuro podría traer un provider nuevo con breaking changes.

> Analogía: el provider es el driver/SDK. `azurerm` es a Azure lo que un driver
> ODBC a una base. Cambiás de provider y hablás con otra nube con el mismo motor.

---

## 5. Resources vs Data sources

- **`resource`**: algo cuyo **ciclo de vida Terraform posee** (crea, cambia,
  destruye). Ej: tu Container App, tu Static Web App, tu ACR.
- **`data`**: una **consulta de solo lectura** a algo que ya existe y que NO querés
  que Terraform gestione. Ej: tu **PostgreSQL Flexible Server actual** — lo
  referenciás para leer su hostname/connection, pero no querés que Terraform lo
  recree ni lo destruya.

```hcl
# Lo posee Terraform:
resource "azurerm_resource_group" "main" {
  name     = "posfifo-dev-rg"
  location = "eastus2"
}

# Solo se lee (ya existe, lo creaste antes / vive aparte):
data "azurerm_postgresql_flexible_server" "db" {
  name                = "posfifo-pg"
  resource_group_name = "grupo-existente-de-la-db"
}
# -> data.azurerm_postgresql_flexible_server.db.fqdn  (lo usás para armar la conn)
```

Esta distinción es clave en tu caso: la DB ya existe, así que entra como `data`,
no como `resource` (no la querés bajo riesgo de `destroy`).

---

## 6. El grafo de dependencias (no ordenás a mano)

Cuando un recurso **referencia** a otro, creás una dependencia implícita:

```hcl
resource "azurerm_container_registry" "acr" {
  resource_group_name = azurerm_resource_group.main.name  # <- depende del RG
  # ...
}
```

Terraform construye un **DAG** (grafo dirigido acíclico) con esas referencias,
ordena solo, y **paraleliza** lo que no depende entre sí. No escribís el orden;
emerge de las referencias. Para dependencias que no se ven en atributos (raras),
existe `depends_on` explícito.

> Analogía: como las dependencias de paquetes, o `After=/Requires=` en systemd.
> Vos declarás relaciones; el motor resuelve el orden.

---

## 7. Variables, outputs, locals

- **`variable`**: entradas del módulo (parámetros). Se setean por `.tfvars`, env
  (`TF_VAR_...`) o flags. Ej: `var.environment`, `var.location`.
- **`output`**: valores que el módulo expone hacia afuera (a vos en consola o a
  otro módulo). Ej: la URL del Static Web App, el login server del ACR.
- **`locals`**: valores calculados/derivados para no repetirte. Ej:
  `local.prefix = "posfifo-${var.environment}"`.

```hcl
variable "environment" { type = string }            # entrada
locals  { prefix = "posfifo-${var.environment}" }   # derivado
output  "acr_login_server" { value = azurerm_container_registry.acr.login_server }  # salida
```

> El trío variable→local→output es el "contrato" de un módulo: qué recibe, qué
> calcula, qué entrega.

---

## 8. Módulos — DRY y la clave para dev/staging/prod (y multi-tenant)

Un **módulo** es una carpeta de `.tf` reutilizable con su contrato de
inputs/outputs. Hay un **root module** (donde corrés `terraform`) que **llama** a
**child modules**.

```text
environments/
  dev/        main.tf  -> llama a los módulos con valores de DEV (root module)
  staging/    main.tf  -> mismos módulos, valores de STAGING
  prod/       main.tf  -> mismos módulos, valores de PROD
modules/
  static-web-app/   <- la "plantilla" del recurso, una sola vez
  container-apps/
  observability/
```

```hcl
# environments/dev/main.tf
module "frontend" {
  source      = "../../modules/static-web-app"
  environment = "dev"
  location    = "eastus2"
}
```

Mismo código de módulo, distintos valores por ambiente. Esto es exactamente lo que
hace que "agregar un cliente/ambiente no requiera rediseñar el deploy" (tu meta
SaaS): el día de mañana un tenant nuevo es otro `environments/clienteX/` que llama
a los mismos módulos.

> Analogía: un módulo es como un **rol de Ansible** o una plantilla parametrizable.
> Lo escribís una vez, lo instanciás con variables.

---

## 9. El ciclo de comandos (lo que vas a teclear)

```text
terraform init      # descarga providers, configura el backend de state, baja módulos.
                    #   -> se corre al empezar y cuando cambian providers/módulos.
terraform fmt       # formatea el HCL (cosmético, pero hacelo).
terraform validate  # valida sintaxis y referencias, sin tocar Azure.
terraform plan      # DRY-RUN: el diff config↔state↔real. Tu red de seguridad.
                    #   plan -out=tfplan  -> guarda el plan exacto para aplicarlo igual.
terraform apply     # ejecuta. Vuelve a planear y pide confirmación (o aplica tfplan).
                    #   -> actualiza el state con lo que pasó.
terraform destroy   # destruye todo lo gestionado por ese state. (Tu botón de costo $0.)
```

Disciplina de oro: **leer el `plan` antes de cada `apply`**. El plan te dice
exactamente qué va a pasar; los sustos vienen de aplicar sin leerlo. Símbolos del
plan:

```text
+ create        (crear)
~ update in-place (cambiar sin recrear)
-/+ replace     (DESTRUIR y recrear — ¡ojo! puede implicar downtime/pérdida de datos)
-  destroy      (eliminar)
```

El `-/+ replace` es el que más hay que vigilar: cambiar un atributo "inmutable"
(p. ej. el nombre de algunos recursos) obliga a destruir y recrear.

---

## 10. Meta-argumentos de ciclo de vida (gotchas útiles)

Dentro de un recurso podés ajustar su comportamiento:

- `lifecycle { prevent_destroy = true }` — red de seguridad contra borrar algo
  crítico (p. ej. la DB) por error.
- `lifecycle { create_before_destroy = true }` — crear el reemplazo antes de tirar
  el viejo (minimiza downtime en `replace`).
- `lifecycle { ignore_changes = [tags] }` — ignorar drift en atributos que cambia
  otra cosa (p. ej. tags que mete una policy).
- `count` / `for_each` — crear N instancias del mismo recurso. (Te va a servir
  para iterar tenants/sucursales el día de mañana.)

---

## 11. Renombrar ≠ gratis: identidad y `moved`

La "dirección" de un recurso (`azurerm_resource_group.main`) **es su identidad en
el state**. Si lo renombrás en el `.tf` a `.principal`, Terraform cree que borraste
uno y creaste otro → propondría destroy+create. Para renombrar sin destruir:

- bloque `moved { from = ... to = ... }` en el código (moderno, declarativo), o
- `terraform state mv` (comando).

Y si creaste algo a mano en el portal y lo querés meter bajo Terraform sin
recrearlo: `terraform import` (o bloque `import`). Es tedioso → por eso conviene
**no hacer ClickOps en lo que pensás gestionar con IaC**.

---

## 12. Gotchas que te van a morder (lista corta)

- **Secretos en el state** → backend remoto cifrado + no commitear `.tfstate`.
- **Drift por ClickOps** → todo cambio por Terraform, o asumí reconciliación.
- **`-/+ replace` inesperado** → leé el plan; algunos atributos son inmutables.
- **Provider sin pin** → fijá versión + commiteá el lockfile.
- **State lock colgado** (si matás un apply) → `terraform force-unlock` con cuidado.
- **Static Web App**: Terraform crea el recurso, pero el **deploy del contenido**
  (el bundle React) sigue yendo por el workflow de ASWA con su token; IaC y
  pipeline de deploy son cosas separadas.
- **Container Apps**: es el recurso más fiddly (ingress, secrets, identity, creds
  de ACR). Dejalo para cuando el flujo básico ya te salga de memoria.

---

## 13. Ejemplo mínimo anotado (ilustrativo, no el esqueleto final)

```hcl
# ---- versiones + provider (el "driver" hacia Azure) ----
terraform {
  required_version = ">= 1.9"
  required_providers {
    azurerm = { source = "hashicorp/azurerm", version = "~> 4.0" }
  }
  # backend "azurerm" { ... }   # <- se agrega al pasar de state local a remoto
}
provider "azurerm" { features {} }

# ---- entradas ----
variable "environment" { type = string, default = "dev" }
variable "location"    { type = string, default = "eastus2" }

# ---- derivados ----
locals { prefix = "posfifo-${var.environment}" }

# ---- recursos que Terraform POSEE ----
resource "azurerm_resource_group" "main" {
  name     = "${local.prefix}-rg"
  location = var.location
}

resource "azurerm_log_analytics_workspace" "logs" {
  name                = "${local.prefix}-logs"
  resource_group_name = azurerm_resource_group.main.name   # <- dependencia implícita
  location            = azurerm_resource_group.main.location
  sku                 = "PerGB2018"
  retention_in_days   = 30
}

# ---- algo que ya existe: solo se LEE ----
data "azurerm_postgresql_flexible_server" "db" {
  name                = "posfifo-pg"
  resource_group_name = "rg-donde-vive-la-db"
}

# ---- salidas ----
output "resource_group" { value = azurerm_resource_group.main.name }
output "db_fqdn"        { value = data.azurerm_postgresql_flexible_server.db.fqdn }
```

Flujo: `az login` → `terraform init` → `terraform plan` (leés el diff: "+ 2 to
create") → `terraform apply` → trabajás → `terraform destroy` (y el costo vuelve a
~0). El state quedó sabiendo qué creó; el segundo `plan` sin cambios diría "No
changes".

---

## 14. Cómo mapea a tu roadmap (D2)

- `environments/dev|staging|prod/` = **root modules**, un **state por ambiente**.
- `modules/static-web-app|container-apps|observability|key-vault|postgres/` =
  **child modules** reutilizables.
- La DB Flexible existente entra como **`data` source** (no `resource`).
- Empezamos por lo **fácil y estable** (RG, ACR, Log Analytics, **Static Web App**)
  con **state local**, y dejamos **Container Apps** para después.
- `floci-lab/` = zona de juegos para `init/plan/apply/destroy` sin costo.

---

## 15. Glosario rápido

| Término | Qué es | Analogía on-prem |
|---|---|---|
| Provider | Plugin que habla con una API (azurerm) | Driver/SDK |
| Resource | Recurso gestionado por TF (ciclo de vida) | Item bajo gestión |
| Data source | Lectura de algo existente | Query read-only al inventario |
| State | Mapeo lógico→real + metadata | CMDB / fuente de verdad |
| Backend | Dónde vive el state (local/Azure Storage) | Repo del CMDB |
| Plan | Diff dry-run config↔state↔real | Change request en simulación |
| Apply | Ejecuta el plan | Ejecutar el cambio aprobado |
| Module | Carpeta reutilizable con inputs/outputs | Rol de Ansible / plantilla |
| Drift | Real ≠ state (cambio fuera de TF) | Cambio sin ticket |
| DAG | Orden derivado de las referencias | Dependencias systemd/paquetes |

---

**Siguiente paso sugerido:** cuando digas, armamos `infra/azure/` empezando por el
módulo `static-web-app` + foundation (RG, observability), con `.tf` comentados
paso a paso y state local, para que tu primer `plan/apply/destroy` sea sobre algo
simple y entendible.
