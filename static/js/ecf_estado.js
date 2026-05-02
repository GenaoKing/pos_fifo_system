/**
 * static/js/ecf_estado.js
 *
 * Componente Alpine.js reutilizable para mostrar el estado del e-CF
 * de una venta con polling automático.
 *
 * Uso en un template:
 *
 *   <div x-data="ecfEstadoBadge({{ venta.id }})" x-init="iniciar()">
 *     <template x-if="cargando">
 *       <span class="badge-info">Consultando e-CF...</span>
 *     </template>
 *     <template x-if="!cargando && tiene_ecf">
 *       <div>
 *         <span :class="badgeClase()" x-text="estado_display"></span>
 *         <p x-show="encf" x-text="'eNCF: ' + encf"></p>
 *         <p x-show="codigo_seguridad"
 *            x-text="'Código de seguridad: ' + codigo_seguridad"></p>
 *         <p x-show="leyendaEnvioDiferido()" class="text-amber-700">
 *           e-CF emitido en modalidad Envío Diferido
 *         </p>
 *       </div>
 *     </template>
 *     <template x-if="!cargando && !tiene_ecf">
 *       <span class="badge-secondary" x-text="mensaje"></span>
 *     </template>
 *   </div>
 *
 * Requiere:
 * - getCsrfToken() y jsonHeaders() globales (de utils.js)
 * - Endpoint GET /facturacion-electronica/api/ecf/estado/<venta_id>/
 *
 * Comportamiento:
 * - Carga inicial al montar (iniciar()).
 * - Si el estado NO es terminal, vuelve a consultar cada POLL_INTERVAL_MS.
 * - Si es terminal (APROBADO / APROBADO_CONDICIONAL / RECHAZADO),
 *   detiene el polling y deja el badge final visible.
 * - Si no hay ECF (módulo inactivo o aún no encolado), muestra
 *   mensaje y NO polletea (el módulo no se activa solo).
 */

const POLL_INTERVAL_MS = 5000;
const MAX_POLLS = 24;  // 24 * 5s = 2 minutos máximo de polling activo

function ecfEstadoBadge(ventaId) {
  return {
    // Estado del componente
    cargando: true,
    tiene_ecf: false,
    estado: null,
    estado_display: '',
    estado_terminal: false,
    tipo: null,
    tipo_display: '',
    encf: null,
    codigo_seguridad: null,
    qr_url: null,
    intentos: 0,
    mensaje: '',
    mensaje_ultimo_evento: '',

    // Control interno
    _ventaId: ventaId,
    _intervalId: null,
    _pollCount: 0,

    // ----------------------------------------------------------- ciclo

    async iniciar() {
      await this.consultar();
      // Si no es terminal, programar polling
      if (this.tiene_ecf && !this.estado_terminal) {
        this._intervalId = setInterval(() => {
          this._pollCount += 1;
          if (this._pollCount >= MAX_POLLS) {
            this.detener();
            return;
          }
          this.consultar();
        }, POLL_INTERVAL_MS);
      }
    },

    detener() {
      if (this._intervalId) {
        clearInterval(this._intervalId);
        this._intervalId = null;
      }
    },

    // ------------------------------------------------------ HTTP

    async consultar() {
      try {
        const response = await fetch(
          `/facturacion-electronica/api/ecf/estado/${this._ventaId}/`,
          { method: 'GET', credentials: 'same-origin' }
        );
        if (!response.ok) {
          this.mensaje = `Error al consultar estado (HTTP ${response.status})`;
          return;
        }
        const data = await response.json();
        this._aplicar(data);
      } catch (err) {
        console.error('ecfEstadoBadge: error de red', err);
        this.mensaje = 'Error de red al consultar estado.';
      } finally {
        this.cargando = false;
      }
    },

    _aplicar(data) {
      this.tiene_ecf = !!data.tiene_ecf;
      if (!this.tiene_ecf) {
        this.mensaje = data.mensaje || 'Sin e-CF asociado.';
        this.detener();
        return;
      }

      this.estado = data.estado;
      this.estado_display = data.estado_display;
      this.estado_terminal = !!data.estado_terminal;
      this.tipo = data.tipo;
      this.tipo_display = data.tipo_display;
      this.encf = data.encf;
      this.codigo_seguridad = data.codigo_seguridad;
      this.qr_url = data.qr_url;
      this.intentos = data.intentos;
      this.mensaje_ultimo_evento = data.mensaje_ultimo_evento;

      if (this.estado_terminal) {
        this.detener();
      }
    },

    // ------------------------------------------------------ helpers UI

    /**
     * Mapea estado a clase CSS de badge. Asume design system del
     * proyecto (badge-success, badge-warning, badge-danger, etc.).
     */
    badgeClase() {
      const m = {
        'PENDIENTE': 'badge-secondary',
        'ENVIADO': 'badge-info',
        'EN_PROCESO': 'badge-info',
        'APROBADO': 'badge-success',
        'APROBADO_CONDICIONAL': 'badge-warning',
        'RECHAZADO': 'badge-danger',
        'ERROR': 'badge-danger',
      };
      return m[this.estado] || 'badge-secondary';
    },

    /**
     * Determina si hay que mostrar la leyenda obligatoria DGII de
     * "Envío Diferido". Aplica cuando el ECF ya tiene encf y código
     * de seguridad pero todavía NO está aprobado por DGII.
     *
     * Si el ECF ni siquiera tiene encf todavía (estado PENDIENTE
     * antes del primer envío a MSeller), no mostramos la leyenda
     * porque el documento aún no es válido fiscalmente.
     */
    leyendaEnvioDiferido() {
      if (!this.encf || !this.codigo_seguridad) {
        return false;
      }
      return !this.estado_terminal || this.estado === 'EN_PROCESO';
    },

    /**
     * El ticket está rechazado por DGII: no debe entregarse como
     * comprobante fiscal válido.
     */
    esRechazado() {
      return this.estado === 'RECHAZADO';
    },

    /**
     * El ECF está aprobado: el ticket impreso es plenamente válido.
     */
    esAprobado() {
      return this.estado === 'APROBADO' ||
             this.estado === 'APROBADO_CONDICIONAL';
    },
  };
}

// Export global para que Alpine lo encuentre por nombre.
// Si el proyecto usa módulos ES6, ajustar a `export { ecfEstadoBadge }`.
window.ecfEstadoBadge = ecfEstadoBadge;