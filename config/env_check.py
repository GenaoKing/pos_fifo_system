"""
config/env_check.py

Validacion de la configuracion de la instalacion, al arrancar.

Existe porque los fallos de configuracion de este sistema historicamente NO se
manifestaban como un error claro, sino como sintomas raros que costaron dias de
diagnostico:

- Un `&` sin comillas trunco el `DJANGO_SECRET_KEY` de un cliente a 5 caracteres.
  El sistema arranco igual; nadie lo noto hasta mucho despues (bug #9).
- Una variable vacia que NSSM pasaba como "definida" rompia los `int(...)` de
  settings con un ValueError sin contexto.
- Un nombre de impresora con espacios llegaba con comillas pegadas al valor.

La regla aqui es simple: **fallar ruidoso y temprano, con el nombre exacto de la
variable**, en vez de arrancar a medias.

Uso desde `server.py`:

    from config.env_check import validar_entorno
    problemas = validar_entorno()
"""
import os

# Sin esta no tiene sentido arrancar: la instalacion queda insegura.
CRITICAS = (
    'DJANGO_SECRET_KEY',
)

# Claves de conexion que deben quedar resueltas. Se validan sobre la
# configuracion EFECTIVA de Django, no sobre el entorno: lo que importa es que
# la BD quede bien configurada, no de donde salio el valor (un settings de
# desarrollo puede traerlos hardcodeados y eso es valido).
CLAVES_BD = ('NAME', 'USER', 'HOST')

# Longitud por debajo de la cual una SECRET_KEY es claramente el resultado de
# haberse truncado. Django genera 50 caracteres; el caso real quedo en 5.
MIN_SECRET_KEY = 20

# Valores de plantilla que nadie deberia dejar en una instalacion real.
PLACEHOLDERS = (
    'CAMBIAR',
    'PEGAR-TOKEN',
    'tu-clave-secreta-aqui',
)


class Problema:
    """Un hallazgo de configuracion. `critico` decide si aborta el arranque."""

    def __init__(self, variable, mensaje, critico=False):
        self.variable = variable
        self.mensaje = mensaje
        self.critico = critico

    def __str__(self):
        marca = 'CRITICO' if self.critico else 'AVISO'
        return f'[{marca}] {self.variable}: {self.mensaje}'


def validar_entorno(entorno=None, databases=None):
    """
    Revisa la configuracion efectiva y devuelve la lista de problemas.

    `databases` es el dict `settings.DATABASES`. Si no se pasa, se intenta leer
    de Django; si tampoco se puede, se omite el chequeo de BD en vez de inventar
    un fallo.

    No lanza ni imprime: quien llama decide que hacer. Asi sirve tanto para
    abortar el arranque como para un reporte de diagnostico.
    """
    env = os.environ if entorno is None else entorno
    problemas = []
    problemas.extend(_revisar_base_datos(databases))

    for nombre in CRITICAS:
        valor = (env.get(nombre) or '').strip()
        if not valor:
            problemas.append(Problema(
                nombre, 'no esta definida y es obligatoria.', critico=True,
            ))
            continue
        if any(p.lower() in valor.lower() for p in PLACEHOLDERS):
            problemas.append(Problema(
                nombre,
                'conserva un valor de plantilla; hay que ponerle el valor real.',
                critico=True,
            ))

    problemas.extend(_revisar_secret_key(env))
    problemas.extend(_revisar_comillas(env))
    problemas.extend(_revisar_coherencia_sync(env))
    return problemas


def _revisar_base_datos(databases):
    """Valida la conexion sobre la configuracion efectiva de Django."""
    if databases is None:
        try:
            from django.conf import settings as dj_settings
            databases = dj_settings.DATABASES
        except Exception:
            return []

    default = (databases or {}).get('default') or {}
    if not default:
        return [Problema('DATABASES', 'no hay conexion por defecto configurada.',
                         critico=True)]

    faltantes = [k for k in CLAVES_BD if not str(default.get(k) or '').strip()]
    if faltantes:
        return [Problema(
            'DATABASES',
            f'la conexion por defecto no tiene {", ".join(faltantes)}. '
            f'Revisar DB_NAME/DB_USER/DB_HOST en el .env.',
            critico=True,
        )]
    return []


def _revisar_secret_key(env):
    """La firma del bug #9: una key corta es una key truncada."""
    key = (env.get('DJANGO_SECRET_KEY') or '').strip()
    if key and len(key) < MIN_SECRET_KEY:
        return [Problema(
            'DJANGO_SECRET_KEY',
            f'solo tiene {len(key)} caracteres. Es la firma de un valor '
            f'truncado por un caracter especial (&, parentesis) sin escapar. '
            f'Revisar el .env y rotarla.',
            critico=True,
        )]
    return []


def _revisar_comillas(env):
    """Residuo de haber pasado por `cmd`: el valor se queda con las comillas."""
    problemas = []
    for nombre, valor in env.items():
        if not isinstance(valor, str) or len(valor) < 2:
            continue
        if valor[0] == valor[-1] and valor[0] in ('"', "'"):
            problemas.append(Problema(
                nombre,
                'el valor viene entre comillas; probablemente se colaron como '
                'parte del dato. Quitarlas en el .env.',
            ))
    return problemas


def _revisar_coherencia_sync(env):
    """Configuracion de sync incoherente: el sintoma de BUG-A."""
    sync = (env.get('SYNC_ENABLED') or '').strip().lower() in ('1', 'true', 'yes', 'on')
    url = (env.get('CLOUD_API_URL') or '').strip()
    token = (env.get('CLOUD_API_TOKEN') or '').strip()

    if (url or token) and not sync:
        return [Problema(
            'SYNC_ENABLED',
            'hay credenciales de cloud configuradas pero el sync esta apagado. '
            'Los eventos se encolan y nunca se envian. Diagnostico: '
            'manage.py verificar_sync',
        )]
    if sync and not (url and token):
        return [Problema(
            'CLOUD_API_URL/CLOUD_API_TOKEN',
            'el sync esta encendido pero falta la URL o el token: los eventos '
            'se acumularan sin destino.',
        )]
    return []


def abortar_si_critico(problemas, escribir=print):
    """
    Imprime los problemas y devuelve True si alguno es critico.

    Se separa de `validar_entorno` para que la validacion siga siendo pura y
    testeable sin capturar stdout.
    """
    if not problemas:
        return False

    criticos = [p for p in problemas if p.critico]
    escribir('')
    escribir('  Revision de configuracion:')
    for p in problemas:
        escribir(f'    {p}')
    if criticos:
        escribir('')
        escribir('  [ERROR] Hay problemas criticos de configuracion. '
                 'Revise deploy/env_cliente.env')
    escribir('')
    return bool(criticos)
