"""
apps/suscripciones/checks.py

System checks del catalogo de modulos. Corren en `manage.py check` (que el
deploy ya ejecuta) y en el arranque del runserver.

El catalogo tiene DOS fuentes: el registro en codigo (`registry.py`, la verdad
del grafo de dependencias y de `core`) y su espejo en la tabla `Modulo`, que los
planes y overrides referencian por FK. SUS-012 documenta que podian divergir en
silencio: un modulo agregado solo en la DB se asignaba a un plan y el API lo
aceptaba, pero el resolutor —que sale del registro— lo eliminaba del set
efectivo; un `core` editado en Admin no cambiaba ninguna regla real; y una key
retirada del registro dejaba filas huerfanas que ningun gate reconoce.

Estos checks convierten esa divergencia silenciosa en un fallo con diff
accionable, en vez de un plan que promete un modulo que runtime no entiende.
"""
from django.core.checks import Error, Warning, register

from . import registry


@register('suscripciones')
def registro_de_modulos_es_valido(app_configs, **kwargs):
    """
    El registro en codigo (`CATALOGO_MODULOS`) tiene que ser internamente
    consistente: sin keys duplicadas, sin dependencias hacia keys inexistentes y
    sin ciclos. Un ciclo o una dependencia colgada no rompe el resolutor —lo
    ignora— pero deja el grafo diciendo algo que no se cumple.
    """
    errores = []
    catalogo = registry.CATALOGO_MODULOS
    keys = [m.key for m in catalogo]
    conocidas = set(keys)

    duplicadas = sorted({k for k in keys if keys.count(k) > 1})
    if duplicadas:
        errores.append(Error(
            f'Keys de modulo duplicadas en el registro: {duplicadas}.',
            hint='Cada modulo debe declararse una sola vez en CATALOGO_MODULOS.',
            id='suscripciones.E001',
        ))

    for m in catalogo:
        colgadas = [dep for dep in m.depende_de if dep not in conocidas]
        if colgadas:
            errores.append(Error(
                f"El modulo '{m.key}' depende de keys inexistentes: {colgadas}.",
                hint='Toda dependencia en depende_de debe existir en CATALOGO_MODULOS.',
                id='suscripciones.E002',
            ))

    ciclo = _detectar_ciclo(catalogo)
    if ciclo:
        errores.append(Error(
            f'El grafo de dependencias de modulos tiene un ciclo: {ciclo}.',
            hint='depende_de no puede formar ciclos.',
            id='suscripciones.E003',
        ))

    return errores


@register('suscripciones')
def espejo_db_coincide_con_registro(app_configs, **kwargs):
    """
    La tabla `Modulo` es un espejo del registro. Debe coincidir key por key:

    - una key en la DB que no esta en el registro (ERROR) es la mas peligrosa:
      un plan/override puede referenciarla y el resolutor la descarta en
      silencio, prometiendo un modulo que runtime no reconoce;
    - una key del registro ausente en la DB (WARNING) significa que falta correr
      `sync_modulos`; el resolutor funciona con el registro, pero los planes no
      pueden incluir ese modulo hasta sembrarlo;
    - `core` divergente entre DB y registro (WARNING) es cosmetico —la regla
      real sale del registro— pero confunde al operador que lo ve en Admin.
    """
    try:
        from .models import Modulo

        db_modulos = {m.key: m for m in Modulo.objects.all()}
    except Exception:
        # Sin BD disponible (build de imagen, collectstatic, primer migrate) el
        # check no aplica.
        return []

    en_codigo = {m.key: m for m in registry.CATALOGO_MODULOS}
    problemas = []

    fantasmas = sorted(set(db_modulos) - set(en_codigo))
    if fantasmas:
        problemas.append(Error(
            f'Modulos en la DB que no existen en el registro: {fantasmas}.',
            hint=(
                'Un plan u override puede referenciarlos y el resolutor los '
                'descarta en silencio. Retira esas filas o agregalas al '
                'registro (registry.py).'
            ),
            id='suscripciones.E004',
        ))

    faltantes = sorted(set(en_codigo) - set(db_modulos))
    if faltantes:
        problemas.append(Warning(
            f'Modulos del registro sin sembrar en la DB: {faltantes}.',
            hint='Corre `manage.py sync_modulos` para sembrarlos.',
            id='suscripciones.W001',
        ))

    core_divergente = sorted(
        key for key in set(en_codigo) & set(db_modulos)
        if db_modulos[key].core != en_codigo[key].core
    )
    if core_divergente:
        problemas.append(Warning(
            f'Modulos con `core` divergente entre DB y registro: {core_divergente}.',
            hint='Corre `manage.py sync_modulos`; el registro es la verdad de `core`.',
            id='suscripciones.W002',
        ))

    return problemas


def _detectar_ciclo(catalogo):
    """DFS: devuelve la primera key involucrada en un ciclo, o None."""
    grafo = {m.key: tuple(m.depende_de) for m in catalogo}
    EN_PROCESO, LISTO = 1, 2
    estado = {}

    def visitar(k):
        if estado.get(k) == LISTO:
            return None
        if estado.get(k) == EN_PROCESO:
            return k
        estado[k] = EN_PROCESO
        for dep in grafo.get(k, ()):
            if dep not in grafo:
                continue  # dependencia colgada: la reporta E002, no este check
            hallado = visitar(dep)
            if hallado:
                return hallado
        estado[k] = LISTO
        return None

    for key in grafo:
        hallado = visitar(key)
        if hallado:
            return hallado
    return None
