/**
 * Módulo de Impresión para POS
 * Sistema POS FIFO 
 * 
 * Maneja la impresión automática de tickets al completar ventas
 * y proporciona funciones para reimpresión y test de impresora.
 * 
 * Uso en el POS:
 *   import { imprimirTicketAutomatico } from './printer.js';
 *   imprimirTicketAutomatico(ventaId);
 * 
 * @author Sistema POS FIFO
 * @version 1.0
 */

/**
 * Imprime un ticket automáticamente tras completar una venta
 * Esta función se llama desde el POS después de guardar la venta
 * 
 * @param {number} ventaId - ID de la venta recién completada
 * @param {Object} options - Opciones adicionales
 * @param {boolean} options.silencioso - Si true, no muestra alertas
 * @param {function} options.onSuccess - Callback de éxito
 * @param {function} options.onError - Callback de error
 * @returns {Promise<Object>} Resultado de la impresión
 */
export async function imprimirTicketAutomatico(ventaId, options = {}) {
    const {
        silencioso = false,
        onSuccess = null,
        onError = null
    } = options;

    try {
        const response = await fetch(`/impresion/ticket/${ventaId}/`, {
            method: 'POST',
            headers: {
                'X-CSRFToken': getCookie('csrftoken'),
                'Content-Type': 'application/json'
            }
        });

        const data = await response.json();

        if (data.success) {
            console.log('✓ Ticket impreso:', data.mensaje);
            
            if (!silencioso) {
                mostrarNotificacion('Ticket impreso exitosamente', 'success');
            }
            
            if (onSuccess) {
                onSuccess(data);
            }
        } else {
            console.warn('⚠ Error imprimiendo:', data.mensaje);
            
            if (!silencioso) {
                mostrarNotificacion(
                    `No se pudo imprimir: ${data.mensaje}`,
                    'warning'
                );
            }
            
            if (onError) {
                onError(data);
            }
        }

        return data;

    } catch (error) {
        console.error('✗ Error de conexión:', error);
        
        if (!silencioso) {
            mostrarNotificacion(
                'Error de conexión con el sistema de impresión',
                'error'
            );
        }
        
        if (onError) {
            onError({ success: false, error: error.message });
        }
        
        return { success: false, error: error.message };
    }
}

/**
 * Reimprime un ticket de una venta anterior
 * Requiere permisos de reimpresión
 * 
 * @param {number} ventaId - ID de la venta a reimprimir
 * @returns {Promise<Object>} Resultado de la reimpresión
 */
export async function reimprimirTicket(ventaId) {
    try {
        const response = await fetch(`/impresion/reimprimir/${ventaId}/`, {
            method: 'POST',
            headers: {
                'X-CSRFToken': getCookie('csrftoken'),
                'Content-Type': 'application/json'
            }
        });

        const data = await response.json();

        if (data.success) {
            mostrarNotificacion('Ticket reimpreso exitosamente', 'success');
        } else {
            mostrarNotificacion(data.mensaje, 'error');
        }

        return data;

    } catch (error) {
        console.error('Error reimprimiendo:', error);
        mostrarNotificacion('Error al reimprimir ticket', 'error');
        return { success: false, error: error.message };
    }
}

/**
 * Ejecuta una prueba de impresión
 * Útil para verificar que la impresora está funcionando
 * 
 * @returns {Promise<Object>} Resultado de la prueba
 */
export async function testImpresora() {
    try {
        mostrarNotificacion('Enviando página de prueba...', 'info');

        const response = await fetch('/impresion/test/', {
            method: 'POST',
            headers: {
                'X-CSRFToken': getCookie('csrftoken'),
                'Content-Type': 'application/json'
            }
        });

        const data = await response.json();

        if (data.success) {
            mostrarNotificacion('Prueba exitosa. Revisa la impresora.', 'success');
        } else {
            mostrarNotificacion(`Prueba falló: ${data.mensaje}`, 'error');
        }

        return data;

    } catch (error) {
        console.error('Error en test:', error);
        mostrarNotificacion('Error ejecutando test de impresora', 'error');
        return { success: false, error: error.message };
    }
}

/**
 * Verifica el estado del sistema de impresión
 * 
 * @returns {Promise<Object>} Estado de la impresora
 */
export async function verificarEstadoImpresora() {
    try {
        const response = await fetch('/impresion/api/estado/');
        return await response.json();
    } catch (error) {
        console.error('Error verificando estado:', error);
        return { enabled: false, error: error.message };
    }
}

/**
 * Obtiene las últimas impresiones realizadas
 * 
 * @param {number} limit - Cantidad de registros (default: 10)
 * @returns {Promise<Object>} Lista de impresiones
 */
export async function obtenerUltimasImpresiones(limit = 10) {
    try {
        const response = await fetch(`/impresion/api/ultimas/?limit=${limit}`);
        return await response.json();
    } catch (error) {
        console.error('Error obteniendo impresiones:', error);
        return { success: false, error: error.message };
    }
}

// ============================================================================
// FUNCIONES DE UTILIDAD
// ============================================================================

/**
 * Obtiene el token CSRF de las cookies
 * 
 * @param {string} name - Nombre de la cookie
 * @returns {string|null} Valor de la cookie
 */
function getCookie(name) {
    let cookieValue = null;
    if (document.cookie && document.cookie !== '') {
        const cookies = document.cookie.split(';');
        for (let i = 0; i < cookies.length; i++) {
            const cookie = cookies[i].trim();
            if (cookie.substring(0, name.length + 1) === (name + '=')) {
                cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                break;
            }
        }
    }
    return cookieValue;
}

/**
 * Muestra una notificación en la interfaz
 * 
 * @param {string} mensaje - Mensaje a mostrar
 * @param {string} tipo - Tipo: 'success', 'error', 'warning', 'info'
 */
function mostrarNotificacion(mensaje, tipo = 'info') {
    // Verificar si existe Alpine.js y el sistema de notificaciones
    if (window.Alpine && window.Alpine.store && window.Alpine.store('notifications')) {
        window.Alpine.store('notifications').add(mensaje, tipo);
        return;
    }

    // Fallback: usar alert nativo
    const iconos = {
        success: '✓',
        error: '✗',
        warning: '⚠',
        info: 'ℹ'
    };
    
    const icono = iconos[tipo] || 'ℹ';
    console.log(`${icono} ${mensaje}`);
    
    // Crear notificación visual simple si no hay sistema de notificaciones
    const notif = document.createElement('div');
    notif.className = `fixed top-4 right-4 p-4 rounded-lg shadow-lg z-50 ${
        tipo === 'success' ? 'bg-green-500' :
        tipo === 'error' ? 'bg-red-500' :
        tipo === 'warning' ? 'bg-yellow-500' :
        'bg-blue-500'
    } text-white`;
    notif.textContent = `${icono} ${mensaje}`;
    document.body.appendChild(notif);
    
    setTimeout(() => {
        notif.remove();
    }, 3000);
}

// ============================================================================
// INTEGRACIÓN CON EL FLUJO DEL POS
// ============================================================================

/**
 * Ejemplo de integración en el flujo de venta del POS
 * 
 * Este código debe agregarse en el archivo pos_views.py o en el 
 * JavaScript del POS después de completar una venta.
 */

/*
// En el template del POS (pos/index.html):

<script type="module">
import { imprimirTicketAutomatico } from '{% static "js/printer.js" %}';

// Después de completar la venta exitosamente
async function completarVenta() {
    // ... lógica de guardado de venta ...
    
    const response = await fetch('/ventas/completar/', {
        method: 'POST',
        // ... datos de la venta
    });
    
    const data = await response.json();
    
    if (data.success) {
        // Imprimir ticket automáticamente
        await imprimirTicketAutomatico(data.venta_id, {
            onSuccess: (resultado) => {
                console.log('Ticket impreso correctamente');
                // Limpiar el carrito, mostrar resumen, etc.
            },
            onError: (error) => {
                // La venta se guardó pero no se imprimió
                // Ofrecer opción de reimprimir
                if (confirm('La venta se guardó pero hubo un error al imprimir. ¿Intentar de nuevo?')) {
                    imprimirTicketAutomatico(data.venta_id);
                }
            }
        });
    }
}
</script>
*/

// ============================================================================
// BOTÓN DE TEST EN INTERFAZ DE ADMINISTRACIÓN
// ============================================================================

/**
 * Agregar este código en el template de configuración del sistema
 * para permitir al admin probar la impresora
 */

/*
<!-- En templates/admin/configuracion.html -->
<button 
    onclick="testearImpresora()"
    class="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700"
>
    🖨️ Probar Impresora
</button>

<script type="module">
import { testImpresora } from '{% static "js/printer.js" %}';

window.testearImpresora = async function() {
    await testImpresora();
}
</script>
*/

// Exportar funciones principales
export default {
    imprimirTicketAutomatico,
    reimprimirTicket,
    testImpresora,
    verificarEstadoImpresora,
    obtenerUltimasImpresiones
};
