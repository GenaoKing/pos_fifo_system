"""
apps/caja/views.py
Vistas del modulo de Arqueo y Gestion de Caja
"""

import json
import logging
from decimal import Decimal, InvalidOperation

from django.http import Http404, JsonResponse
from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth import authenticate

from apps.permisos.decorators import requiere_permiso_json, requiere_permiso_local
from apps.permisos.models import AutorizacionInvalida, AutorizacionOverride

logger = logging.getLogger('caja')
from django.utils import timezone
from django.db.models import Sum, Count, Q
from django.db import transaction
from apps.sync import events as sync_events

from .models import Caja, TurnoCaja, MovimientoCaja

from django.db import transaction
from apps.sync import events as sync_events



def es_admin(user, sucursal=None):
    """
    True si el usuario administra caja EN ESTA SUCURSAL.

    Se llamaba sin sucursal, y el motor RBAC solo acota las asignaciones cuando
    recibe una: con `None`, un rol concedido unicamente para la sucursal A
    habilitaba el gate en la B.
    """
    return user.is_authenticated and user.tiene_permiso(
        'caja.administrar', sucursal=sucursal,
    )


def cajas_en_alcance(request):
    """
    Cajas que este usuario puede operar.

    La pagina listaba TODAS las cajas activas y la apertura recuperaba
    cualquiera por PK: en una BD compartida, un operador podia abrir la caja de
    otra sucursal.

    Las cajas legacy (`sucursal` nula, anteriores a la Fase 2) quedan visibles:
    darlas por ajenas las volveria inoperables en instalaciones sin migrar.
    """
    base = Caja.objects.filter(activa=True)
    sucursal = getattr(request, 'sucursal', None)
    if sucursal is None:
        return base
    return base.filter(Q(sucursal=sucursal) | Q(sucursal__isnull=True))


def turnos_en_alcance(request):
    """Turnos de las cajas que el usuario puede ver."""
    return TurnoCaja.objects.filter(caja__in=cajas_en_alcance(request))


# ============================================================================
# SOFT-LOGIN: Validar credenciales de admin sin cambiar sesion
# ============================================================================

@login_required
def api_validar_admin(request):
    """
    POST: valida las credenciales de un autorizador y EMITE una autorizacion
    puntual.

    Antes devolvia el `admin_id` crudo y el cliente lo reenviaba con la
    operacion. Ese ID no probaba nada: cualquiera que conociera (o adivinara,
    son enteros secuenciales) el id de un administrador podia atribuirle una
    excepcion que nunca aprobo. La auditoria registraba su nombre igual.

    Ahora devuelve un token de un solo uso, de vida corta y ligado a la
    operacion, al operador que lo pide, a la sucursal, al monto y al alcance.
    Ver `apps.permisos.models.AutorizacionOverride`.

    Dos formas de credencial, ambas equivalentes:

      - `username` + `password`: teclear las credenciales.
      - `credencial`: pasar un carnet por el lector del POS. Evita teclear una
        contrasena delante del cliente y de la cola, que es la razon por la que
        en la practica esas contrasenas terminan siendo "1234" o compartidas.

    Body: {
        "username": "admin", "password": "...",   # forma A
        "credencial": "<codigo escaneado>",       # forma B
        "operacion": "credito.exceder_limite" | "caja.retiro" | "ventas.descuento",
        "motivo": "...",              # obligatorio salvo config (ver abajo)
        "monto": "1500.00",           # opcional, acota el token
        "cliente_id": 5               # opcional, acota el token
    }
    Returns: { "valido": true, "token": "...", "expira_en_minutos": 5,
               "admin_nombre": "Admin" }
    """
    from apps.configuracion.utils import get_config
    from apps.permisos import throttling
    from apps.permisos.models import AutorizacionOverride, CredencialFisica

    if request.method != 'POST':
        return JsonResponse({'error': 'Metodo no permitido'}, status=405)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'valido': False, 'error': 'Datos invalidos'}, status=400)

    username = data.get('username', '')
    password = data.get('password', '')
    credencial = (data.get('credencial') or '').strip()
    operacion = (data.get('operacion') or AutorizacionOverride.OP_CAJA_RETIRO).strip()
    motivo = (data.get('motivo') or '').strip()

    operaciones_validas = dict(AutorizacionOverride.OPERACIONES)
    if operacion not in operaciones_validas:
        return JsonResponse({'valido': False, 'error': 'Operacion no autorizable.'})

    # -------- Motivo: obligatorio por defecto, configurable para descuentos
    # Sin motivo la traza dice QUIEN aprobo pero no POR QUE, asi que la regla
    # general es exigirlo. La excepcion son los descuentos: donde se regatea,
    # casi toda venta lleva descuento y el texto libre degenera en 400 filas
    # que dicen "descuento". Lo decide el negocio en su configuracion.
    vigencia_minutos = None
    if operacion == AutorizacionOverride.OP_VENTA_DESCUENTO:
        config = get_config()
        motivo_obligatorio = config.descuento_motivo_obligatorio
        vigencia_minutos = config.descuento_vigencia_minutos
    else:
        motivo_obligatorio = True

    if motivo_obligatorio and not motivo:
        return JsonResponse({
            'valido': False,
            'error': 'El motivo de la autorizacion es obligatorio.',
        })

    # -------- Freno de fuerza bruta, para las dos formas de credencial
    if throttling.excedido(request):
        return JsonResponse({
            'valido': False,
            'error': 'Demasiados intentos fallidos. Espera unos minutos.',
        }, status=429)

    if credencial:
        user = CredencialFisica.resolver(credencial)
        error_credencial = 'Credencial no reconocida'
    elif username and password:
        # Autenticar sin tocar la sesion
        user = authenticate(request, username=username, password=password)
        error_credencial = 'Credenciales incorrectas'
    else:
        return JsonResponse({'valido': False, 'error': 'Credenciales requeridas'})

    if user is None:
        throttling.registrar_fallo(request)
        return JsonResponse({'valido': False, 'error': error_credencial})

    # `CredencialFisica.resolver` ya descarta usuarios inactivos; para la forma
    # con contrasena el chequeo sigue haciendo falta.
    if not user.is_active or not getattr(user, 'activo', True):
        throttling.registrar_fallo(request)
        return JsonResponse({'valido': False, 'error': 'Usuario inactivo'})

    throttling.limpiar(request)

    permiso = AutorizacionOverride.PERMISO_REQUERIDO[operacion]
    if not user.tiene_permiso(permiso, sucursal=getattr(request, 'sucursal', None)):
        return JsonResponse({
            'valido': False,
            'error': 'El usuario no tiene permiso para autorizar esta operacion.',
        })

    monto = data.get('monto')
    alcance = {}
    if data.get('cliente_id'):
        alcance['cliente_id'] = data['cliente_id']

    _, token = AutorizacionOverride.emitir(
        operacion=operacion,
        autorizado_por=user,
        solicitado_por=request.user,
        sucursal=getattr(request, 'sucursal', None),
        monto_maximo=Decimal(str(monto)) if monto not in (None, '') else None,
        alcance=alcance,
        motivo=motivo,
        minutos=vigencia_minutos,
    )

    return JsonResponse({
        'valido': True,
        'token': token,
        'expira_en_minutos': vigencia_minutos or AutorizacionOverride.VIGENCIA_MINUTOS,
        'admin_nombre': user.get_short_name() or user.username,
    })


# ============================================================================
# PAGINA PRINCIPAL DE CAJA
# ============================================================================

@login_required
@requiere_permiso_local('caja.operar', redirect_to='pos:punto_venta')
def caja_index(request):
    """
    Pagina principal del modulo de caja.
    Muestra el turno activo del usuario o la opcion de abrir uno.
    """
    turno_activo = TurnoCaja.objects.filter(
        usuario=request.user,
        estado='ABIERTO'
    ).select_related('caja').first()

    # Cajas disponibles (sin turno abierto), acotadas a la sucursal.
    cajas_disponibles = cajas_en_alcance(request).exclude(
        turnos__estado='ABIERTO'
    )

    # Historial de turnos del usuario (ultimos 10)
    historial = TurnoCaja.objects.filter(
        usuario=request.user,
        estado='CERRADO'
    ).select_related('caja')[:10]

    # Si es admin, mostrar los turnos abiertos DE SU ALCANCE.
    puede_administrar = es_admin(request.user, getattr(request, 'sucursal', None))
    turnos_abiertos_otros = None
    if puede_administrar:
        turnos_abiertos_otros = turnos_en_alcance(request).filter(
            estado='ABIERTO'
        ).exclude(
            usuario=request.user
        ).select_related('caja', 'usuario')

    # Desglose y movimientos del turno activo (si existe)
    desglose = None
    movimientos = []
    if turno_activo:
        desglose = turno_activo.calcular_esperado()
        movimientos = turno_activo.movimientos.all().select_related(
            'registrado_por', 'autorizado_por'
        ).order_by('-fecha')

    context = {
        'turno_activo': turno_activo,
        'cajas_disponibles': cajas_disponibles,
        'historial': historial,
        'turnos_abiertos_otros': turnos_abiertos_otros,
        'desglose': desglose,
        'movimientos': movimientos,
        # La plantilla decidia con `request.user.rol == 'ADMIN'`, el campo
        # legacy; el servidor decide con el permiso RBAC acotado a sucursal.
        # Con RBAC activo eran dos respuestas distintas: la UI le escondia el
        # boton a un admin por permiso y se lo mostraba a un ADMIN legacy sin
        # permiso, que despues chocaba contra un 403.
        'puede_administrar_caja': puede_administrar,
    }

    # Datos hidratados para Alpine.js (se renderiza con |json_script en template)
    init_data = {
        'cajas_disponibles': list(cajas_disponibles.values('id', 'nombre')),
    }

    if turno_activo:
        init_data['turno'] = {
            'id': turno_activo.id,
            'caja': turno_activo.caja.nombre,
            'apertura': turno_activo.fecha_apertura.strftime('%d/%m/%Y %H:%M'),
            'fondo_apertura': str(turno_activo.fondo_apertura),
        }
        init_data['desglose'] = {k: str(v) for k, v in desglose.items()}
        init_data['movimientos'] = [{
            'id': m.id,
            'tipo': m.tipo,
            'tipo_display': m.get_tipo_display(),
            'monto': str(m.monto),
            'descripcion': m.descripcion,
            'fecha': m.fecha.strftime('%d/%m/%Y %H:%M'),
            'registrado_por': m.registrado_por.get_short_name() or m.registrado_por.username,
            'autorizado_por': (m.autorizado_por.get_short_name() or m.autorizado_por.username) if m.autorizado_por else None,
        } for m in movimientos]

    context['init_data_json'] = init_data  # sin json.dumps: el filtro |json_script lo hace

    return render(request, 'caja/index.html', context)


# ============================================================================
# ABRIR TURNO
# ============================================================================


@login_required
@requiere_permiso_json('caja.operar')
def api_abrir_turno(request):
    """
    POST: Abre un nuevo turno de caja.
    Body: { "caja_id": 1, "fondo_apertura": 2000.00, "notas": "..." }
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Metodo no permitido'}, status=405)

    try:
        with transaction.atomic():
            data = json.loads(request.body)
            caja_id = data.get('caja_id')
            fondo = Decimal(str(data.get('fondo_apertura', 0)))
            notas = data.get('notas', '')

            # Validar que no tenga turno abierto
            turno_existente = TurnoCaja.objects.filter(
                usuario=request.user,
                estado='ABIERTO'
            ).first()

            if turno_existente:
                return JsonResponse({
                    'success': False,
                    'error': f'Ya tienes un turno abierto en {turno_existente.caja.nombre}'
                }, status=400)

            # El fondo de apertura no puede ser negativo: un importe
            # imposible se arrastra a todo el arqueo del turno.
            if fondo < Decimal('0'):
                return JsonResponse({
                    'success': False,
                    'error': 'El fondo de apertura no puede ser negativo.',
                }, status=400)

            # La caja se resuelve CONTRA el alcance: un id de otra sucursal da 404.
            caja = get_object_or_404(cajas_en_alcance(request), id=caja_id)

            # Validar que la caja no tenga turno abierto
            if caja.turno_activo():
                return JsonResponse({
                    'success': False,
                    'error': f'{caja.nombre} ya tiene un turno abierto'
                }, status=400)

            # Crear turno
            turno = TurnoCaja.objects.create(
                caja=caja,
                usuario=request.user,
                fondo_apertura=fondo,
                notas_apertura=notas,
            )

            # Outbox transaccional: atomico con la apertura del turno.
            sync_events.evento_apertura_caja(turno)

            return JsonResponse({
                'success': True,
                'turno': {
                    'id': turno.id,
                    'caja': caja.nombre,
                    'fondo': str(turno.fondo_apertura),
                    'fecha': turno.fecha_apertura.strftime('%d/%m/%Y %H:%M'),
                }
            })

    except Http404:
        # `get_object_or_404` contra el alcance: el recurso ajeno debe salir
        # como 404, no ser tragado por el `except Exception` de abajo y
        # convertirse en un 500 que parece una falla del servidor.
        raise
    except json.JSONDecodeError:
        return JsonResponse(
            {'success': False, 'error': 'JSON invalido en el request.'}, status=400,
        )
    except (InvalidOperation, TypeError, ValueError) as exc:
        # Un importe mal formado es error del cliente, no del servidor: caia en
        # el `except Exception` y salia como 500 exponiendo la excepcion.
        return JsonResponse({'success': False, 'error': str(exc)}, status=400)
    except Exception:
        logger.exception('Error inesperado en una operacion de caja')
        return JsonResponse(
            {'success': False, 'error': 'Error inesperado en la operacion de caja.'},
            status=500,
        )


# ============================================================================
# CERRAR TURNO
# ============================================================================

@login_required
@requiere_permiso_json('caja.operar')
def api_cerrar_turno(request):
    """
    POST: Cierra el turno activo.
    Body: { "monto_contado": 15000.00, "notas": "..." }

    La cajera cierra su propio turno.
    Un admin puede cerrar cualquier turno (con turno_id).
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Metodo no permitido'}, status=405)

    try:
        with transaction.atomic():
            data = json.loads(request.body)
            monto_contado = Decimal(str(data.get('monto_contado', 0)))
            notas = data.get('notas', '')
            turno_id = data.get('turno_id')  # Opcional, para admin cerrando turno de otro

            # El conteo final no puede ser negativo: no existe una caja con
            # menos de cero pesos fisicos, y un valor imposible se propaga a la
            # diferencia y al arqueo.
            if monto_contado < Decimal('0'):
                return JsonResponse({
                    'success': False,
                    'error': 'El monto contado no puede ser negativo.',
                }, status=400)

            # Determinar cual turno cerrar. El turno se toma CON LOCK: sin el,
            # dos cierres simultaneos leian ambos `ABIERTO`, los dos calculaban
            # el esperado y los dos cerraban — con arqueos distintos segun el
            # orden de commit. El lock tambien congela el turno mientras se
            # calcula lo esperado, para que una venta que entre a mitad quede
            # inequivocamente antes o despues del corte.
            base = turnos_en_alcance(request).select_for_update()

            if turno_id and es_admin(request.user, getattr(request, "sucursal", None)):
                turno = base.filter(id=turno_id, estado='ABIERTO').first()
                if turno is None:
                    return JsonResponse({
                        'success': False,
                        'error': 'Turno no encontrado o ya cerrado.',
                    }, status=404)
            else:
                turno = base.filter(usuario=request.user, estado='ABIERTO').first()

            if not turno:
                return JsonResponse({
                    'success': False,
                    'error': 'No hay turno abierto para cerrar'
                }, status=400)

            # Revalidacion BAJO el lock: si otro request lo cerro mientras
            # esperabamos, aca ya se ve cerrado.
            if turno.estado != 'ABIERTO':
                return JsonResponse({
                    'success': False,
                    'error': 'El turno ya fue cerrado.',
                }, status=409)

            # Cerrar turno. `cerrar()` ya calcula el esperado; se reutiliza
            # para el resumen y evitar recomputarlo bajo el lock del turno.
            calculo = turno.cerrar(
                monto_contado=monto_contado,
                cerrado_por=request.user,
                notas=notas
            )

            # Mismo snapshot para la respuesta local y el payload de sync: el
            # cajero y el destinatario remoto ven exactamente las mismas cifras.
            resumen = turno.resumen_operativo(efectivo=calculo)

            # Outbox transaccional: atomico con el cierre del turno.
            sync_events.evento_cierre_caja(turno, resumen=resumen)

            return JsonResponse({
                'success': True,
                'cierre': {
                    'turno_id': turno.id,
                    'caja': turno.caja.nombre,
                    'cajero': turno.usuario.get_short_name() or turno.usuario.username,
                    'apertura': turno.fecha_apertura.strftime('%d/%m/%Y %H:%M'),
                    'cierre': turno.fecha_cierre.strftime('%d/%m/%Y %H:%M'),
                    'cantidad_ventas': resumen['cantidad_ventas'],
                    'total_ventas': str(resumen['total_ventas']),
                    'pagos_por_metodo': {
                        metodo: str(monto)
                        for metodo, monto in resumen['pagos_por_metodo'].items()
                    },
                    'cobros_cxc_total': str(resumen['cobros_cxc_total']),
                    'cobros_cxc_por_metodo': {
                        metodo: str(monto)
                        for metodo, monto in resumen['cobros_cxc_por_metodo'].items()
                    },
                    **{
                        clave: str(resumen[clave])
                        for clave in (
                            'fondo_apertura', 'efectivo_ventas', 'efectivo_cxc',
                            'retiros', 'gastos', 'ingresos', 'esperado',
                            'contado', 'diferencia',
                        )
                    },
                }
            })

    except Http404:
        # `get_object_or_404` contra el alcance: el recurso ajeno debe salir
        # como 404, no ser tragado por el `except Exception` de abajo y
        # convertirse en un 500 que parece una falla del servidor.
        raise
    except json.JSONDecodeError:
        return JsonResponse(
            {'success': False, 'error': 'JSON invalido en el request.'}, status=400,
        )
    except (InvalidOperation, TypeError, ValueError) as exc:
        # Un importe mal formado es error del cliente, no del servidor: caia en
        # el `except Exception` y salia como 500 exponiendo la excepcion.
        return JsonResponse({'success': False, 'error': str(exc)}, status=400)
    except Exception:
        logger.exception('Error inesperado en una operacion de caja')
        return JsonResponse(
            {'success': False, 'error': 'Error inesperado en la operacion de caja.'},
            status=500,
        )


# ============================================================================
# REGISTRAR MOVIMIENTO DE CAJA
# ============================================================================

@login_required
@requiere_permiso_json('caja.operar')
def api_registrar_movimiento(request):
    """
    POST: Registra un movimiento de caja (retiro, gasto, ingreso).
    Body: {
        "tipo": "RETIRO",
        "monto": 5000.00,
        "descripcion": "Retiro para deposito bancario",
        "override_token": "..."  // Requerido para RETIRO/INGRESO si no es admin
        "turno_id": 3            // Solo admin: operar sobre el turno de otro
    }
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Metodo no permitido'}, status=405)

    try:
        with transaction.atomic():
            data = json.loads(request.body)
            tipo = data.get('tipo', '').upper()
            monto = Decimal(str(data.get('monto', 0)))
            descripcion = data.get('descripcion', '').strip()
            turno_id = data.get('turno_id')

            # Validaciones
            if tipo not in ('RETIRO', 'GASTO', 'INGRESO'):
                return JsonResponse({'success': False, 'error': 'Tipo invalido'}, status=400)

            if monto <= 0:
                return JsonResponse({'success': False, 'error': 'Monto debe ser mayor a 0'}, status=400)

            if not descripcion:
                return JsonResponse({'success': False, 'error': 'Descripcion requerida'}, status=400)

            # Turno destino.
            #
            # ANTES el turno propio ganaba SIEMPRE: `turno_id` solo se miraba
            # si el usuario no tenia turno abierto. Un admin con su propia caja
            # abierta que pedia registrar un gasto en el turno de una cajera
            # veia "listo" y el movimiento aterrizaba en SU turno. Dos arqueos
            # quedaban mal — uno con un gasto que no le corresponde y otro sin
            # el que si — y nada en la respuesta delataba el desvio.
            #
            # Ahora un `turno_id` explicito manda, y solo lo puede usar quien
            # administra caja en esta sucursal.
            if turno_id:
                if not es_admin(request.user, getattr(request, 'sucursal', None)):
                    return JsonResponse({
                        'success': False,
                        'error': 'No puedes registrar movimientos en el turno de otro.',
                    }, status=403)
                turno = get_object_or_404(
                    turnos_en_alcance(request), id=turno_id, estado='ABIERTO',
                )
            else:
                turno = TurnoCaja.objects.filter(
                    usuario=request.user, estado='ABIERTO',
                ).first()

            if not turno:
                return JsonResponse({
                    'success': False,
                    'error': 'No hay turno abierto'
                }, status=400)

            # RETIRO e INGRESO requieren autorizacion admin.
            #
            # ANTES bastaba con enviar `admin_id`: el endpoint comprobaba que
            # existiera un Usuario activo con rol ADMIN y lo persistia como
            # `autorizado_por`. Nunca verificaba que ese ID viniera de
            # `api_validar_admin`, asi que una cajera podia retirar efectivo
            # atribuyendole la aprobacion a cualquier administrador cuyo ID
            # conociera o adivinara — y el historial afirmaba una autorizacion
            # que jamas ocurrio.
            #
            # Ahora se exige el token de un solo uso que emite el soft-login,
            # ligado a operacion, operador, sucursal y monto, y se consume
            # dentro de esta misma transaccion.
            autorizado_por = None
            if tipo in ('RETIRO', 'INGRESO'):
                if es_admin(request.user, getattr(request, "sucursal", None)):
                    # Un admin operando su propia caja se auto-autoriza: su
                    # sesion YA es la prueba de identidad.
                    autorizado_por = request.user
                else:
                    try:
                        autorizacion = AutorizacionOverride.consumir(
                            token=data.get('override_token'),
                            operacion=AutorizacionOverride.OP_CAJA_RETIRO,
                            solicitado_por=request.user,
                            monto=monto,
                            referencia=f'MovCaja turno {turno.id}',
                        )
                    except AutorizacionInvalida as exc:
                        return JsonResponse(
                            {'success': False, 'error': str(exc)}, status=403,
                        )
                    autorizado_por = autorizacion.autorizado_por
                    # El permiso se revalida AL CONSUMIR, no solo al emitir: el
                    # autorizador pudo perderlo entre una cosa y la otra.
                    if not autorizado_por.tiene_permiso(
                        'caja.administrar', sucursal=getattr(request, 'sucursal', None),
                    ):
                        return JsonResponse({
                            'success': False,
                            'error': 'El autorizador ya no tiene permiso sobre caja.',
                        }, status=403)
                    descripcion = (
                        f'{descripcion} [Autorizado: {autorizacion.motivo}]'
                    ).strip()

            # Crear movimiento
            movimiento = MovimientoCaja.objects.create(
                turno=turno,
                tipo=tipo,
                monto=monto,
                descripcion=descripcion,
                registrado_por=request.user,
                autorizado_por=autorizado_por,
            )

            # Recalcular esperado
            desglose = turno.calcular_esperado()
            # Outbox transaccional: atomico con el movimiento.
            sync_events.evento_movimiento_caja(movimiento)

            return JsonResponse({
                'success': True,
                'movimiento': {
                    'id': movimiento.id,
                    'tipo': movimiento.get_tipo_display(),
                    'monto': str(movimiento.monto),
                    'descripcion': movimiento.descripcion,
                    'fecha': movimiento.fecha.strftime('%d/%m/%Y %H:%M'),
                    'autorizado_por': autorizado_por.get_short_name() if autorizado_por else None,
                },
                'desglose': {
                    'fondo_apertura': str(desglose['fondo_apertura']),
                    'efectivo_ventas': str(desglose['efectivo_ventas']),
                    'efectivo_cxc': str(desglose['efectivo_cxc']),
                    'retiros': str(desglose['retiros']),
                    'gastos': str(desglose['gastos']),
                    'ingresos': str(desglose['ingresos']),
                    'esperado': str(desglose['esperado']),
                }
            })

    except Http404:
        # `get_object_or_404` contra el alcance: el recurso ajeno debe salir
        # como 404, no ser tragado por el `except Exception` de abajo y
        # convertirse en un 500 que parece una falla del servidor.
        raise
    except json.JSONDecodeError:
        return JsonResponse(
            {'success': False, 'error': 'JSON invalido en el request.'}, status=400,
        )
    except (InvalidOperation, TypeError, ValueError) as exc:
        # Un importe mal formado es error del cliente, no del servidor: caia en
        # el `except Exception` y salia como 500 exponiendo la excepcion.
        return JsonResponse({'success': False, 'error': str(exc)}, status=400)
    except Exception:
        logger.exception('Error inesperado en una operacion de caja')
        return JsonResponse(
            {'success': False, 'error': 'Error inesperado en la operacion de caja.'},
            status=500,
        )


# ============================================================================
# ESTADO ACTUAL DEL TURNO (para polling/refresh)
# ============================================================================

@login_required
@requiere_permiso_json('caja.operar')
def api_estado_turno(request):
    """
    GET: Retorna el estado actual del turno abierto del usuario.
    Usado para refrescar la UI sin recargar pagina.
    """
    turno = TurnoCaja.objects.filter(
        usuario=request.user,
        estado='ABIERTO'
    ).select_related('caja').first()

    if not turno:
        return JsonResponse({'tiene_turno': False})

    desglose = turno.calcular_esperado()
    movimientos = turno.movimientos.select_related(
        'registrado_por', 'autorizado_por'
    ).order_by('-fecha')[:20]

    return JsonResponse({
        'tiene_turno': True,
        'turno': {
            'id': turno.id,
            'caja': turno.caja.nombre,
            'apertura': turno.fecha_apertura.strftime('%d/%m/%Y %H:%M'),
            'fondo_apertura': str(turno.fondo_apertura),
        },
        'desglose': {
            'fondo_apertura': str(desglose['fondo_apertura']),
            'efectivo_ventas': str(desglose['efectivo_ventas']),
            'efectivo_cxc': str(desglose['efectivo_cxc']),
            'retiros': str(desglose['retiros']),
            'gastos': str(desglose['gastos']),
            'ingresos': str(desglose['ingresos']),
            'esperado': str(desglose['esperado']),
        },
        'movimientos': [{
            'id': m.id,
            'tipo': m.tipo,
            'tipo_display': m.get_tipo_display(),
            'monto': str(m.monto),
            'descripcion': m.descripcion,
            'fecha': m.fecha.strftime('%d/%m/%Y %H:%M'),
            'registrado_por': m.registrado_por.get_short_name() or m.registrado_por.username,
            'autorizado_por': (m.autorizado_por.get_short_name() or m.autorizado_por.username) if m.autorizado_por else None,
        } for m in movimientos],
    })


# ============================================================================
# HISTORIAL DE TURNOS (Admin)
# ============================================================================

@login_required
@requiere_permiso_local('caja.operar', redirect_to='pos:punto_venta')
def historial_turnos(request):
    """
    Pagina de historial de turnos (solo admin).
    """
    if not es_admin(request.user, getattr(request, "sucursal", None)):
        from django.shortcuts import redirect
        return redirect('caja:index')

    turnos = turnos_en_alcance(request).filter(
        estado='CERRADO'
    ).select_related('caja', 'usuario', 'cerrado_por').order_by('-fecha_cierre')[:50]

    context = {
        'turnos': turnos,
    }

    return render(request, 'caja/historial.html', context)


# ============================================================================
# DETALLE DE TURNO CERRADO
# ============================================================================

@login_required
@requiere_permiso_json('caja.operar')
def api_detalle_turno(request, turno_id):
    """
    GET: Detalle completo de un turno cerrado.
    """
    turno = get_object_or_404(turnos_en_alcance(request), id=turno_id)

    # Solo admin o el propio usuario puede ver el detalle
    if not es_admin(request.user, getattr(request, "sucursal", None)) and turno.usuario != request.user:
        return JsonResponse({'error': 'Sin permisos'}, status=403)

    movimientos = turno.movimientos.select_related(
        'registrado_por', 'autorizado_por'
    ).order_by('fecha')

    return JsonResponse({
        'turno': {
            'id': turno.id,
            'caja': turno.caja.nombre,
            'cajero': turno.usuario.get_short_name() or turno.usuario.username,
            'apertura': turno.fecha_apertura.strftime('%d/%m/%Y %H:%M'),
            'cierre': turno.fecha_cierre.strftime('%d/%m/%Y %H:%M') if turno.fecha_cierre else None,
            'fondo_apertura': str(turno.fondo_apertura),
            # `if turno.monto_esperado` trataba Decimal('0.00') como
            # ausencia: un turno que cerro en cero — el caso que MAS conviene
            # revisar — se mostraba como "sin dato", indistinguible de un turno
            # abierto. La ausencia real es NULL.
            'esperado': (
                str(turno.monto_esperado) if turno.monto_esperado is not None else None
            ),
            'contado': (
                str(turno.monto_contado) if turno.monto_contado is not None else None
            ),
            'diferencia': str(turno.diferencia) if turno.diferencia is not None else None,
            'estado': turno.estado,
            'cerrado_por': (turno.cerrado_por.get_short_name() or turno.cerrado_por.username) if turno.cerrado_por else None,
            'notas_apertura': turno.notas_apertura,
            'notas_cierre': turno.notas_cierre,
        },
        'movimientos': [{
            'tipo': m.tipo,
            'tipo_display': m.get_tipo_display(),
            'monto': str(m.monto),
            'descripcion': m.descripcion,
            'fecha': m.fecha.strftime('%d/%m/%Y %H:%M'),
            'registrado_por': m.registrado_por.get_short_name() or m.registrado_por.username,
            'autorizado_por': (m.autorizado_por.get_short_name() or m.autorizado_por.username) if m.autorizado_por else None,
        } for m in movimientos],
    })
