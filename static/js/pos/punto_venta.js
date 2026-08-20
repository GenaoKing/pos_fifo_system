/**
 * POS - Punto de Venta
 * JavaScript Component (Alpine.js)
 * 
 * PARTE 2: Descuentos + Panel de Pago
 */

function posData() {
    return {
        // ============================================
        // ESTADO DEL CARRITO
        // ============================================
        carrito: [],
        
        // ============================================
        // ESTADO DE BÚSQUEDA
        // ============================================
        busqueda: '',
        productos: [],
        loading: false,
        accesosRapidos: [],
        loadingAccesosRapidos: false,
        
       // ============================================
        // ESTADO DEL SCANNER
        // ============================================
        scannerProcessing: false,
        scannerState: 'ready',
        scannerToastVisible: false,
        scannerToastMessage: '',
        scannerToastType: 'success',
        scannerToastTimeout: null,
        scannerFocusInterval: null,

        // ============================================
        // ESTADO DE TARJETA
        referenciaTarjeta: '',

        // ============================================
        // ESTADO DE CLIENTE Y COTIZACION
        // ============================================
        clienteSeleccionado: null,
        clienteBusqueda: '',
        clientesResultados: [],
        mostrarClientesDropdown: false,
        cotizacionId: null,
        creditoResumen: null,
        creditoConsultando: false,

        // ============================================
        // CONFIGURACIÓN DE MÉTODOS DE PAGO 
        // (CARGADA DESDE EL BACKEND)
        metodosPagoConfig: [],
        metodosCreditoConfig: [],
        permiteMixto: true,
        permitirInvNegativo: false,

        // ============================================
        // ESTADO DE PAGO (NUEVO EN PARTE 2)
        // ============================================
        panelPagoActivo: false,
        metodoPago: 'efectivo', // 'efectivo', 'transferencia', 'mixto'
        moduloEcfActivo: false,
        tipoEcf: '32',
        montoPagado: 0,
        montoEfectivo: 0,
        montoTransferencia: 0,
        cambio: 0,
        modalCreditoAbierto: false,
        credito: {
            modalidad: 'VENCIMIENTO_UNICO',
            metodo_plazo_id: '',
            monto_inicial: 0,
            metodo_inicial: 'efectivo',
            cantidad_cuotas: 1,
            interes_porcentaje: 0,
            frecuencia: 'MENSUAL',
            fecha_primer_vencimiento: '',
            admin_override_id: null,
            admin_override_nombre: '',
            motivo_override: '',
            auth_username: '',
            auth_password: '',
            auth_error: '',
            auth_validando: false,
        },
        
        procesandoVenta: false,
        
        // ============================================
        // INICIALIZACIÓN
        // ============================================
        init() {
            console.log('POS inicializado - Parte 2: Descuentos + Pago');
            console.log('Permitir Inventario Negativo:', this.permitirInvNegativo);
          
            const posConfig = JSON.parse(
                document.getElementById('pos-config-data').textContent
            );
            this.metodosPagoConfig = posConfig.metodos_pago;
            this.metodosCreditoConfig = posConfig.metodos_credito || [];
            this.permiteMixto = posConfig.permite_mixto;
            this.permitirInvNegativo = posConfig.permitir_inventario_negativo;
            this.moduloEcfActivo = posConfig.modulo_ecf;
            if (this.metodosCreditoConfig.length > 0) {
                const metodo = this.metodoCreditoUnico() || this.metodosCreditoConfig[0];
                this.aplicarMetodoCredito(metodo);
            }

            this.cargarAccesosRapidos();
            this.initScanner();

            // Redirigir focus al scanner cuando se hace click fuera de inputs
            document.addEventListener('click', (e) => {
                if (!this.panelPagoActivo && 
                    e.target.tagName !== 'INPUT' && 
                    e.target.tagName !== 'BUTTON' &&
                    e.target.tagName !== 'SELECT' &&
                    e.target.tagName !== 'TEXTAREA') {
                    this.focusScanner();
                }
            });

            // Focus automático en el campo de búsqueda
            this.$nextTick(() => {
                this.$refs.searchInput.focus();
            });

            const urlParams = new URLSearchParams(window.location.search);
            const cotizacionId = urlParams.get('cotizacion');
            if (cotizacionId) {
                this.cargarCotizacion(cotizacionId);
            }
            
            // Atajos de teclado globales
            document.addEventListener('keydown', async (e) => {
                // F2: Focus en búsqueda
                if (e.key === 'F2') {
                    e.preventDefault();
                    if (!this.panelPagoActivo) {
                        this.$refs.searchInput.focus();
                    }
                }
                
                // F4: Proceder a pago / Confirmar venta
                if (e.key === 'F4') {
                    e.preventDefault();
                    if (!this.panelPagoActivo && this.validarCarrito()) {
                        this.habilitarPanelPago();
                    } else if (this.panelPagoActivo && this.validarPago()) {
                        this.confirmarVenta();
                    }
                }
                
                // F8: Cancelar
                // F8: Cancelar
                if (e.key === 'F8') {
                    e.preventDefault();
                    if (this.panelPagoActivo) {
                        this.cancelarPago();
                    } else if (this.carrito.length > 0) {
                        const ok = await showConfirm(
                            '¿Cancelar la venta actual?',
                            'Se vaciará el carrito completo.',
                            { confirmText: 'Sí, cancelar', type: 'danger' }
                        );
                        if (ok) this.limpiarCarrito();
                    }
                }
            });
        },
        
        // ============================================
        // BÚSQUEDA DE PRODUCTOS
        // ============================================
        
        /**
         * Busca productos via API
         * Búsqueda por nombre, SKU o código de barras
         */
        async buscarProductos(options = {}) {
            const categoriaId = options.categoriaId || null;

            if (this.busqueda.trim().length < 2 && !categoriaId) {
                this.productos = [];
                return;
            }
            
            this.loading = true;
            
            try {
                const params = new URLSearchParams({
                    q: this.busqueda,
                    limit: '20',
                });
                if (categoriaId) {
                    params.set('categoria_id', categoriaId);
                }

                const response = await fetch(`/pos/api/buscar/?${params.toString()}`);
                const data = await response.json();
                
                if (data.success) {
                    this.productos = data.productos;
                }
            } catch (error) {
                console.error('Error al buscar productos:', error);
                showToast('error', 'Error al buscar productos');
            } finally {
                this.loading = false;
            }
        },

        async cargarAccesosRapidos() {
            this.loadingAccesosRapidos = true;

            try {
                const response = await fetch('/pos/api/accesos-rapidos/');
                const data = await response.json();

                if (data.success) {
                    this.accesosRapidos = data.accesos;
                }
            } catch (error) {
                console.error('Error al cargar accesos rapidos:', error);
            } finally {
                this.loadingAccesosRapidos = false;
            }
        },

        async usarAccesoRapido(acceso) {
            if (this.panelPagoActivo) return;

            if (acceso.tipo === 'categoria') {
                this.busqueda = acceso.categoria_nombre || acceso.etiqueta;
                await this.buscarProductos({ categoriaId: acceso.categoria_id });
                this.$refs.searchInput.focus();
                return;
            }

            if (acceso.tipo !== 'producto' || !acceso.producto_id) return;

            try {
                const response = await fetch(`/pos/api/producto-id/${acceso.producto_id}/`);
                const data = await response.json();

                if (data.success) {
                    const agregado = this.agregarProductoAlCarrito(data.producto);
                    if (agregado) {
                        showToast('success', `${data.producto.nombre} agregado`);
                    }
                } else {
                    showToast('error', data.error || 'No se pudo cargar el producto');
                }
            } catch (error) {
                console.error('Error al usar acceso rapido:', error);
                showToast('error', 'Error al cargar el acceso rapido');
            } finally {
                this.focusScanner();
            }
        },

        clasesAccesoRapido(acceso) {
            const colores = {
                azul: 'border-blue-200 bg-blue-50 text-blue-800 hover:bg-blue-100',
                verde: 'border-emerald-200 bg-emerald-50 text-emerald-800 hover:bg-emerald-100',
                ambar: 'border-amber-200 bg-amber-50 text-amber-800 hover:bg-amber-100',
                gris: 'border-gray-200 bg-gray-50 text-gray-800 hover:bg-gray-100',
            };
            return colores[acceso.color] || colores.azul;
        },

        // ============================================
        // GESTION DEL CARRITO
        // ============================================

        construirItemCarrito(producto) {
            return {
                id: producto.id,
                nombre: producto.nombre,
                sku: producto.sku,
                precio_venta: producto.precio_venta,
                cantidad: 1,
                stock_disponible: producto.stock_disponible,
                descuento: 0,
                subtotal: producto.precio_venta,
            };
        },

        agregarProductoAlCarrito(producto, options = {}) {
            const bloquearSinStock = options.bloquearSinStock !== false;

            if (bloquearSinStock && !producto.tiene_stock && !this.permitirInvNegativo) {
                showToast('error', `${producto.nombre} no tiene stock disponible`);
                return null;
            }

            const index = this.carrito.findIndex(item => item.id === producto.id);

            if (index !== -1) {
                this.carrito[index].cantidad++;
                this.actualizarSubtotal(index);
                return { index, item: this.carrito[index], nuevo: false };
            }

            this.carrito.push(this.construirItemCarrito(producto));
            const nuevoIndex = this.carrito.length - 1;
            return { index: nuevoIndex, item: this.carrito[nuevoIndex], nuevo: true };
        },

        /**
         * Agrega un producto al carrito
         * Si ya existe, incrementa la cantidad
         */
        agregarAlCarrito(producto) {
            const agregado = this.agregarProductoAlCarrito(producto);
            if (!agregado) return;

            this.busqueda = '';
            this.productos = [];
            this.$refs.searchInput.focus();
        },
        
        /**
         * Elimina un producto del carrito
         */
        eliminarDelCarrito(index) {
            this.carrito.splice(index, 1);
            if (this.carrito.length === 0) {
                this.panelPagoActivo = false;
            }
        },
        
        /**
         * Actualiza la cantidad de un item
         */
        actualizarCantidad(index, nuevaCantidad) {
            if (nuevaCantidad < 1) return;
            
            this.carrito[index].cantidad = nuevaCantidad;
            this.actualizarSubtotal(index);
        },
        
        /**
         * Actualiza el subtotal de un item
         */
        actualizarSubtotal(index) {
            const item = this.carrito[index];
            item.subtotal = item.cantidad * item.precio_venta;
            
            // Si el descuento es mayor al nuevo subtotal, ajustarlo
            if (item.descuento > item.subtotal) {
                item.descuento = item.subtotal;
            }
        },
        

        /**
         * Aplica un descuento a un item del carrito
         * Valida que no sea mayor al subtotal
         */
        aplicarDescuento(index) {
            const item = this.carrito[index];
            
            // Validar que no sea negativo
            if (item.descuento < 0) {
                item.descuento = 0;
            }
            
            // Validar que no sea mayor al subtotal
            if (item.descuento > item.subtotal) {
                item.descuento = item.subtotal;
            }
        },
        
        /**
         * Limpia todo el carrito
         */
        limpiarCarrito() {
            this.carrito = [];
            this.busqueda = '';
            this.productos = [];
            this.panelPagoActivo = false;
            this.tipoEcf = '32';
            // Limpiar cliente y cotizacion
            this.clienteSeleccionado = null;
            this.clienteBusqueda = '';
            this.clientesResultados = [];
            this.creditoResumen = null;
            this.cotizacionId = null;
            this.focusScanner();
            this.referenciaTarjeta = '';
            this.resetCredito();
            this.procesandoVenta = false;
        },
        
        // ============================================
        // CÁLCULOS DE TOTALES
        // ============================================
        
        /**
         * Calcula el subtotal (sin descuentos)
         */
        calcularSubtotal() {
            return this.carrito.reduce((sum, item) => sum + item.subtotal, 0);
        },
        
        /**
         * Calcula el total de descuentos
         */
        calcularDescuentoTotal() {
            return this.carrito.reduce((sum, item) => sum + (item.descuento || 0), 0);
        },
        
        /**
         * Calcula el total final (subtotal - descuentos)
         */
        calcularTotal() {
            return this.calcularSubtotal() - this.calcularDescuentoTotal();
        },
        
        // ============================================
        // GESTIÓN DE PAGO
        // ============================================
        
        /**
         * Habilita el panel de pago
         */


        // ============================================
        // GESTION DE CLIENTE
        // ============================================

        /**
         * Buscar clientes para el selector del POS
         */
        async buscarClientes() {
            if (this.clienteBusqueda.length < 2) {
                this.clientesResultados = [];
                this.mostrarClientesDropdown = false;
                return;
            }
            try {
                const res = await fetch(`/clientes/api/buscar/?q=${encodeURIComponent(this.clienteBusqueda)}`);
                const data = await res.json();
                this.clientesResultados = data.clientes || [];
                this.mostrarClientesDropdown = true;
            } catch (e) {
                console.error('Error buscando clientes:', e);
            }
        },

        /**
         * Selecciona un cliente en el POS
         */
        async seleccionarClientePOS(cliente) {
            this.clienteSeleccionado = cliente;
            this.clienteBusqueda = '';
            this.clientesResultados = [];
            this.mostrarClientesDropdown = false;
            await this.consultarCreditoCliente();
        },

        limpiarClientePOS() {
            this.clienteSeleccionado = null;
            this.clienteBusqueda = '';
            this.creditoResumen = null;
            this.resetCreditoAutorizacion();
        },

        async consultarCreditoCliente() {
            if (!this.clienteSeleccionado) {
                this.creditoResumen = null;
                return;
            }
            this.creditoConsultando = true;
            try {
                const res = await fetch(`/cuentas-por-cobrar/api/cliente/${this.clienteSeleccionado.id}/resumen/`);
                const data = await res.json();
                if (data.success) {
                    this.creditoResumen = data;
                }
            } catch (e) {
                console.error('Error consultando credito:', e);
            } finally {
                this.creditoConsultando = false;
            }
        },

        resetCredito() {
            const metodo = this.metodoCreditoUnico() || this.metodosCreditoConfig[0] || null;
            this.credito = {
                modalidad: metodo && metodo.tipo === 'CUOTAS' ? 'CUOTAS' : 'VENCIMIENTO_UNICO',
                metodo_plazo_id: metodo ? metodo.id : '',
                monto_inicial: 0,
                metodo_inicial: 'efectivo',
                cantidad_cuotas: metodo ? (metodo.cantidad_cuotas || 1) : 1,
                interes_porcentaje: metodo ? (parseFloat(metodo.interes_porcentaje) || 0) : 0,
                frecuencia: metodo ? (metodo.frecuencia || 'MENSUAL') : 'MENSUAL',
                fecha_primer_vencimiento: '',
                admin_override_id: null,
                admin_override_nombre: '',
                motivo_override: '',
                auth_username: '',
                auth_password: '',
                auth_error: '',
                auth_validando: false,
            };
            this.modalCreditoAbierto = false;
        },

        resetCreditoAutorizacion() {
            this.credito.admin_override_id = null;
            this.credito.admin_override_nombre = '';
            this.credito.auth_username = '';
            this.credito.auth_password = '';
            this.credito.auth_error = '';
            this.credito.auth_validando = false;
        },

        metodoCreditoSeleccionado() {
            const actual = this.metodosCreditoConfig.find(m => String(m.id) === String(this.credito.metodo_plazo_id)) || null;
            if (this.credito.modalidad === 'VENCIMIENTO_UNICO') {
                return actual && actual.tipo === 'VENCIMIENTO_UNICO' ? actual : this.metodoCreditoUnico();
            }
            if (this.credito.modalidad === 'CUOTAS') {
                return actual && actual.tipo === 'CUOTAS' ? actual : this.metodosCreditoCuotas()[0] || null;
            }
            return actual;
        },

        metodoCreditoUnico() {
            return this.metodosCreditoConfig.find(m => m.tipo === 'VENCIMIENTO_UNICO') || null;
        },

        metodosCreditoCuotas() {
            return this.metodosCreditoConfig.filter(m => m.tipo === 'CUOTAS');
        },

        esCreditoCuotas() {
            return this.credito.modalidad === 'CUOTAS';
        },

        clientePlazoCreditoDias() {
            const valor = this.creditoResumen?.plazo_credito_dias || this.clienteSeleccionado?.plazo_credito_dias || 30;
            return Math.max(parseInt(valor, 10) || 30, 1);
        },

        aplicarMetodoCredito(metodo) {
            if (!metodo) return;
            this.credito.metodo_plazo_id = metodo.id;
            this.credito.modalidad = metodo.tipo === 'CUOTAS' ? 'CUOTAS' : 'VENCIMIENTO_UNICO';
            this.credito.cantidad_cuotas = metodo.cantidad_cuotas || 1;
            this.credito.interes_porcentaje = parseFloat(metodo.interes_porcentaje) || 0;
            this.credito.frecuencia = metodo.frecuencia || 'MENSUAL';
        },

        onModalidadCreditoChange() {
            const metodo = this.esCreditoCuotas()
                ? (this.metodosCreditoCuotas()[0] || this.metodosCreditoConfig[0])
                : (this.metodoCreditoUnico() || this.metodosCreditoConfig[0]);
            this.aplicarMetodoCredito(metodo);
            if (!this.esCreditoCuotas()) {
                this.credito.fecha_primer_vencimiento = '';
                this.credito.cantidad_cuotas = 1;
            }
        },

        saldoCreditoNuevo() {
            return Math.max(this.calcularTotal() - (parseFloat(this.credito.monto_inicial) || 0), 0);
        },

        // Redondeo a 2 decimales HALF_UP (paridad con _q() del backend para montos positivos)
        round2(valor) {
            return Math.round((valor + Number.EPSILON) * 100) / 100;
        },

        interesPorcentaje() {
            const pct = parseFloat(this.credito.interes_porcentaje);
            if (isNaN(pct) || pct < 0) return 0;
            return Math.min(pct, 100);
        },

        montoInteresNuevo() {
            return this.round2(this.saldoCreditoNuevo() * this.interesPorcentaje() / 100);
        },

        // Capital + interes: lo que el cliente realmente debera (igual que el backend)
        montoFinanciadoNuevo() {
            return this.round2(this.saldoCreditoNuevo() + this.montoInteresNuevo());
        },

        cantidadCuotasEfectiva() {
            const metodo = this.metodoCreditoSeleccionado();
            if (!metodo || !this.esCreditoCuotas()) return 1;
            return Math.max(parseInt(this.credito.cantidad_cuotas, 10) || 1, 1);
        },

        etiquetaFrecuencia() {
            const metodo = this.metodoCreditoSeleccionado();
            const etiquetas = {
                SEMANAL: 'semanal',
                QUINCENAL: 'quincenal',
                MENSUAL: 'mensual',
                DIAS: metodo ? `cada ${metodo.dias_vencimiento} dias` : 'cada N dias',
            };
            return etiquetas[this.credito.frecuencia] || this.credito.frecuencia;
        },

        diasEntreCuotas() {
            if (!this.esCreditoCuotas()) return 0;
            if (this.credito.frecuencia === 'SEMANAL') return 7;
            if (this.credito.frecuencia === 'QUINCENAL') return 15;
            if (this.credito.frecuencia === 'MENSUAL') return 30;
            const metodo = this.metodoCreditoSeleccionado();
            return Math.max(parseInt(metodo ? metodo.dias_vencimiento : 30, 10) || 1, 1);
        },

        /**
         * Preview del calendario de cuotas. Replica la regla del backend
         * (cuota base redondeada, la ultima absorbe la diferencia), pero es
         * solo informativo: la fuente de verdad es crear_cuenta_para_venta.
         */
        creditoPreview() {
            const metodo = this.metodoCreditoSeleccionado();
            if (!metodo) return [];

            const financiado = this.montoFinanciadoNuevo();
            const n = this.cantidadCuotasEfectiva();
            const base = this.round2(financiado / n);
            const montos = Array(n).fill(base);
            montos[n - 1] = this.round2(base + (financiado - this.round2(base * n)));

            let primera;
            if (!this.esCreditoCuotas()) {
                primera = new Date();
                primera.setDate(primera.getDate() + this.clientePlazoCreditoDias());
            } else if (this.credito.fecha_primer_vencimiento) {
                const [y, m, d] = this.credito.fecha_primer_vencimiento.split('-').map(Number);
                primera = new Date(y, m - 1, d);
            } else {
                primera = new Date();
                primera.setDate(primera.getDate() + (parseInt(metodo.dias_vencimiento, 10) || 30));
            }
            const intervalo = this.diasEntreCuotas();

            return montos.map((monto, i) => {
                const fecha = new Date(primera);
                fecha.setDate(fecha.getDate() + intervalo * i);
                return {
                    numero: i + 1,
                    fecha: fecha.toLocaleDateString('es-DO', { day: '2-digit', month: '2-digit', year: 'numeric' }),
                    monto: monto,
                };
            });
        },

        abrirModalCredito() {
            if (!this.clienteSeleccionado) {
                showToast('warning', 'Selecciona un cliente para vender a credito');
                return;
            }
            this.consultarCreditoCliente();
            this.modalCreditoAbierto = true;
        },

        cerrarModalCredito() {
            this.modalCreditoAbierto = false;
        },

        // Enter en el modal = boton Aplicar: respeta el mismo disabled
        // (limite excedido sin override admin) y no hace nada si esta cerrado.
        aplicarModalCredito() {
            if (!this.modalCreditoAbierto) return;
            if (this.creditoExcedeLimite() && !this.credito.admin_override_id) return;
            this.cerrarModalCredito();
        },

        creditoDisponible() {
            return this.creditoResumen ? parseFloat(this.creditoResumen.credito_disponible || 0) : 0;
        },

        creditoExcedeLimite() {
            return this.metodoPago === 'credito' && this.creditoResumen && this.montoFinanciadoNuevo() > this.creditoDisponible();
        },

        async validarAdminCredito() {
            this.credito.auth_error = '';
            this.credito.auth_validando = true;
            try {
                const res = await fetch('/caja/api/validar-admin/', {
                    method: 'POST',
                    headers: jsonHeaders(),
                    body: JSON.stringify({
                        username: this.credito.auth_username,
                        password: this.credito.auth_password,
                    }),
                });
                const data = await res.json();
                if (!data.valido) {
                    this.credito.auth_error = data.error || 'No autorizado';
                    return;
                }
                this.credito.admin_override_id = data.admin_id;
                this.credito.admin_override_nombre = data.admin_nombre;
                this.credito.auth_password = '';
                showToast('success', 'Credito autorizado por admin');
            } finally {
                this.credito.auth_validando = false;
            }
        },

        // ============================================
        // CARGA DE COTIZACION
        // ============================================

        /**
         * Carga una cotizacion pendiente al carrito
         */
        async cargarCotizacion(cotizacionId) {
            try {
                const res = await fetch(`/cotizaciones/api/${cotizacionId}/datos/`);
                const data = await res.json();

                if (data.success) {
                    this.carrito = data.productos.map(p => ({
                        id: p.id,
                        nombre: p.nombre,
                        sku: p.sku,
                        precio_venta: p.precio_venta,
                        cantidad: p.cantidad,
                        stock_disponible: p.stock_actual,
                        descuento: p.descuento || 0,
                        subtotal: p.precio_venta * p.cantidad,
                    }));

                    this.cotizacionId = cotizacionId;

                    if (data.cliente) {
                        this.clienteSeleccionado = data.cliente;
                        await this.consultarCreditoCliente();
                    }

                    this.scannerFeedbackMsg('success',
                        `Cotizacion ${data.numero_cotizacion} cargada (${data.productos.length} productos)`
                    );
                } else {
                    this.scannerFeedbackMsg('error', data.error || 'Error al cargar cotizacion');
                }
            } catch (e) {
                console.error('Error cargando cotizacion:', e);
                this.scannerFeedbackMsg('error', 'Error de conexion al cargar cotizacion');
            }
        },


        habilitarPanelPago() {
            if (!this.validarCarrito()) {
                showToast('warning', 'Revisa el carrito antes de proceder al pago');
                return;
            }
            
            this.panelPagoActivo = true;
            this.metodoPago = 'efectivo';
            this.montoPagado = 0;
            this.montoEfectivo = 0;
            this.montoTransferencia = 0;
            this.cambio = 0;
            this.resetCreditoAutorizacion();
            
            // Focus en input de monto
            this.$nextTick(() => {
                if (this.$refs.montoPagadoInput) {
                    this.$refs.montoPagadoInput.focus();
                }
            });
        },
        
        /**
         * Cancela el pago y vuelve al carrito
         */
        cancelarPago() {
            this.panelPagoActivo = false;
            this.modalCreditoAbierto = false;
            this.$nextTick(() => {
                this.focusScanner();
            });
        },
        
        /**
         * Selecciona un método de pago
         */
        seleccionarMetodoPago(metodo) {
            this.metodoPago = metodo;
            this.montoPagado = 0;
            this.montoEfectivo = 0;
            this.montoTransferencia = 0;
            this.cambio = 0;
            if (metodo === 'credito') {
                this.consultarCreditoCliente();
                if (this.clienteSeleccionado) {
                    this.modalCreditoAbierto = true;
                }
            }
            
            // Focus en el input correspondiente
            this.$nextTick(() => {
                if (metodo === 'efectivo' && this.$refs.montoPagadoInput) {
                    this.$refs.montoPagadoInput.focus();
                }
            });
        },
        
        /**
         * Calcula el cambio según el método de pago
         */
        calcularCambio() {
            const total = this.calcularTotal();
            
            if (this.metodoPago === 'efectivo') {
                this.cambio = this.montoPagado - total;
            } else if (this.metodoPago === 'mixto') {
                const totalPagado = this.montoEfectivo + this.montoTransferencia;
                this.cambio = totalPagado - total;
            } else {
                this.cambio = 0;
            }
        },
        
        /**
         * Valida que el carrito esté listo para pagar
         */
        validarCarrito() {
            if (this.carrito.length === 0) return false;
            
            if (!this.permitirInvNegativo) {
                for (let item of this.carrito) {
                    if (item.cantidad > item.stock_disponible) return false;
                }
            }
            return true;
        },
                
        /**
         * Valida que el pago esté completo
         */
        validarPago() {
            const total = this.calcularTotal();
            
            if (this.metodoPago === 'efectivo') {
                return this.montoPagado >= total;
            } else if (this.metodoPago === 'transferencia') {
                return true;  // Monto exacto
            } else if (this.metodoPago === 'tarjeta') {
                return true;  // Monto exacto por terminal
            } else if (this.metodoPago === 'mixto') {
                const totalMixto = this.montoEfectivo + this.montoTransferencia;
                return totalMixto >= total;
            } else if (this.metodoPago === 'credito') {
                const metodo = this.metodoCreditoSeleccionado();
                if (!this.clienteSeleccionado || !metodo) return false;
                if (this.credito.monto_inicial < 0 || this.credito.monto_inicial >= total) return false;
                if (this.esCreditoCuotas() && this.credito.cantidad_cuotas < 1) return false;
                if (this.creditoExcedeLimite() && !this.credito.admin_override_id) return false;
                return true;
            }
            return false;
        },
        
        /**
         * Obtiene el mensaje de validación de pago
         */
        obtenerMensajeValidacion() {
            const total = this.calcularTotal();
            
            if (this.metodoPago === 'efectivo') {
                if (this.montoPagado < total) {
                    const falta = total - this.montoPagado;
                    return `Falta $${falta.toFixed(2)} para completar el pago`;
                }
            } else if (this.metodoPago === 'mixto') {
                const totalPagado = this.montoEfectivo + this.montoTransferencia;
                if (totalPagado < total) {
                    const falta = total - totalPagado;
                    return `Falta $${falta.toFixed(2)} para completar el pago`;
                }
            } else if (this.metodoPago === 'credito') {
                if (!this.clienteSeleccionado) return 'Selecciona un cliente para venta a credito';
                if (!this.metodoCreditoSeleccionado()) return 'No hay metodos de credito configurados';
                if (this.creditoExcedeLimite() && !this.credito.admin_override_id) {
                    return 'Credito excede limite y requiere autorizacion ADMIN';
                }
            }
            
            return 'Completa los datos de pago';
        },
        
        /**
         * Confirma la venta (Parte 3: enviar al backend)
         */
        async confirmarVenta() {
            if (!this.validarPago()) {
                showToast('warning', 'El monto pagado es insuficiente');
                return;
            }

            if (this.moduloEcfActivo && this.tipoEcf === '31') {
                if (!this.clienteSeleccionado || !this.clienteSeleccionado.cedula_rnc) {
                    showToast(
                        'error',
                        'Credito Fiscal (31) requiere cliente con RNC asignado.'
                    );
                    return;
                }
            }
            
            // Preparar datos de la venta
            const datosVenta = {
                carrito: this.carrito.map(item => ({
                    id: item.id,
                    cantidad: item.cantidad,
                    precio_venta: item.precio_venta,
                    descuento: item.descuento || 0
                })),
                cliente_id: this.clienteSeleccionado?.id || null,  // <-- NUEVA LINEA
                metodo_pago: this.metodoPago,
                monto_efectivo: this.metodoPago === 'efectivo' ? this.montoPagado : 
                                this.metodoPago === 'mixto' ? this.montoEfectivo : 0,
                monto_transferencia: this.metodoPago === 'transferencia' ? this.calcularTotal() : 
                                    this.metodoPago === 'mixto' ? this.montoTransferencia : 0,
                monto_tarjeta: this.metodoPago === 'tarjeta' ? this.calcularTotal() : 0,
                referencia_tarjeta: this.referenciaTarjeta,
                cotizacion_id: this.cotizacionId || null,
                total: this.calcularTotal(),
                tipo_ecf: this.tipoEcf || '32',
            };

            if (this.metodoPago === 'credito') {
                const metodoCredito = this.metodoCreditoSeleccionado();
                datosVenta.credito = {
                    modalidad: this.credito.modalidad,
                    metodo_plazo_id: metodoCredito ? metodoCredito.id : this.credito.metodo_plazo_id,
                    monto_inicial: this.credito.monto_inicial || 0,
                    metodo_inicial: this.credito.metodo_inicial,
                    cantidad_cuotas: this.cantidadCuotasEfectiva(),
                    interes_porcentaje: String(this.interesPorcentaje()),
                    frecuencia: this.credito.frecuencia,
                    fecha_primer_vencimiento: this.esCreditoCuotas() ? (this.credito.fecha_primer_vencimiento || null) : null,
                    admin_override_id: this.credito.admin_override_id,
                    motivo_override: this.credito.motivo_override || '',
                    monto_efectivo: this.credito.metodo_inicial === 'efectivo' ? (this.credito.monto_inicial || 0) : 0,
                    monto_transferencia: this.credito.metodo_inicial === 'transferencia' ? (this.credito.monto_inicial || 0) : 0,
                    monto_tarjeta: this.credito.metodo_inicial === 'tarjeta' ? (this.credito.monto_inicial || 0) : 0,
                };
            }
            
            console.log('📤 Enviando venta al backend:', datosVenta);
            
            try {
                // Mostrar loading
                this.procesandoVenta = true;
                
                // Enviar al backend
                const response = await fetch('/pos/api/procesar-venta/', {
                    method: 'POST',
                    headers: jsonHeaders(),
                    body: JSON.stringify(datosVenta)
                });
                
                const data = await response.json();
                
                if (data.success) {
                    // ✅ VENTA EXITOSA
                    console.log('✅ Venta procesada:', data.venta);

                    // NOTA: la cotizacion de origen ya quedo marcada como
                    // CONVERTIDA y vinculada a la venta DENTRO de la misma
                    // transaccion del servidor (se envia `cotizacion_id` en el
                    // payload). Antes esto era un segundo request desde aca: si
                    // se perdia, la cotizacion seguia PENDIENTE y se podia
                    // vender dos veces.

                    // Mostrar confirmación
                    showToast('success',
                        '🎉 ¡VENTA EXITOSA!\n\n' +
                        `Número: ${data.venta.numero_venta}\n` +
                        `Total: $${data.venta.total.toFixed(2)}\n` +
                        `Fecha: ${data.venta.fecha}\n\n` +
                        `${data.mensaje}`
                    );
                    this.procesandoVenta = false;
                    // Limpiar carrito
                    this.limpiarCarrito();

                    // Opcional: Redirigir a página de confirmación
                    // window.location.href = `/pos/venta/${data.venta.id}/exito/`;
                    
                } else {
                    // ❌ ERROR EN LA VENTA
                    console.error('❌ Error:', data.error);
                    showToast('error', `Error al procesar la venta:\n\n${data.error}`);
                    
                    // Restaurar botón
                    this.procesandoVenta = false;
                
                }
                
            } catch (error) {
                console.error('❌ Error de red:', error);
                showToast('error', 'Error de conexión al procesar la venta.\nVerifica tu conexión e intenta nuevamente.');
                
                 this.procesandoVenta = false;
            }
        },
         
        
        // ============================================
        // SCANNER DE CODIGO DE BARRAS
        // ============================================
        // ============================================
        // SCANNER DE CODIGO DE BARRAS
        // ============================================
        
        /**
         * Inicializa el scanner usando un input hidden dedicado.
         * 
         * El scanner 2Connect escribe en el campo que tenga focus.
         * En vez de interceptar keydown (que pelea con Alpine x-model),
         * mantenemos un input INVISIBLE siempre con focus.
         * El scanner escribe ahi y nosotros leemos cuando llega Enter.
         */
        initScanner() {
            console.log('Scanner inicializado - Modo: Input Dedicado');
            
            // Focus inicial en el scanner input
            this.$nextTick(() => {
                this.focusScanner();
            });
            
            // Mantener focus en el scanner input cuando no hay otro input activo
            // Verificamos cada 300ms si el focus se perdio
            this.scannerFocusInterval = setInterval(() => {
                if (this.panelPagoActivo) return;
                
                const activeEl = document.activeElement;
                const isSearchInput = activeEl === this.$refs.searchInput;
                const isScannerInput = activeEl === this.$refs.scannerInput;
                const isOtherInput = activeEl && (
                    activeEl.tagName === 'INPUT' || 
                    activeEl.tagName === 'SELECT' || 
                    activeEl.tagName === 'TEXTAREA'
                );
                
                // Si no hay ningun input enfocado, enfocar el scanner
                if (!isSearchInput && !isScannerInput && !isOtherInput) {
                    this.focusScanner();
                }
            }, 300);
            
            // Cuando el campo de busqueda pierde el focus, 
            // redirigir al scanner input
            this.$refs.searchInput.addEventListener('blur', () => {
                // Pequeno delay para permitir clicks en botones
                setTimeout(() => {
                    if (!this.panelPagoActivo) {
                        const activeEl = document.activeElement;
                        const isInput = activeEl && (
                            activeEl.tagName === 'INPUT' || 
                            activeEl.tagName === 'SELECT' || 
                            activeEl.tagName === 'TEXTAREA' ||
                            activeEl.tagName === 'BUTTON'
                        );
                        if (!isInput) {
                            this.focusScanner();
                        }
                    }
                }, 100);
            });
            
            // Interceptar F2 para cambiar entre scanner input y search input
            document.addEventListener('keydown', (e) => {
                if (e.key === 'F2' && !this.panelPagoActivo) {
                    e.preventDefault();
                    // Si estamos en el scanner input, ir al search
                    if (document.activeElement === this.$refs.scannerInput) {
                        this.$refs.searchInput.focus();
                    } else {
                        // Si estamos en search, volver al scanner
                        this.busqueda = '';
                        this.focusScanner();
                    }
                }
            });
        },
        
        /**
         * Enfoca el input invisible del scanner
         */
        focusScanner() {
            if (this.$refs.scannerInput) {
                this.$refs.scannerInput.value = '';
                this.$refs.scannerInput.focus();
            }
        },
        
        /**
         * Procesa el contenido del input del scanner cuando llega Enter.
         * Se dispara via @keydown.enter en el input hidden.
         */
        async procesarScanDesdeInput() {
            const scannerInput = this.$refs.scannerInput;
            if (!scannerInput) return;
            
            const codigo = scannerInput.value.trim();
            scannerInput.value = '';
            
            // Ignorar si esta vacio o muy corto
            if (codigo.length < 3) return;
            
            // Ignorar si estamos en panel de pago
            if (this.panelPagoActivo) return;
            
            // Evitar doble procesamiento
            if (this.scannerProcessing) return;
            this.scannerProcessing = true;
            this.scannerState = 'scanning';
            
            console.log('Escaneado: ' + codigo);
            
            try {
                const response = await fetch(
                    '/pos/api/producto/' + encodeURIComponent(codigo) + '/'
                );
                const data = await response.json();
                
                if (data.success) {
                    const producto = data.producto;
                    const agregado = this.agregarProductoAlCarrito(producto, {
                        bloquearSinStock: false,
                    });

                    if (agregado.nuevo) {
                        this.scannerFeedbackMsg('success', producto.nombre + ' agregado');
                    } else {
                        this.scannerFeedbackMsg(
                            'success',
                            producto.nombre + ' (x' + agregado.item.cantidad + ')'
                        );
                    }
                    
                    // Alerta de stock si aplica
                    if (producto.stock_disponible <= 0) {
                        // Segundo toast de warning
                        setTimeout(() => {
                            this.scannerFeedbackMsg(
                                'warning', 
                                'Sin stock: ' + producto.nombre
                            );
                        }, 1500);
                    }
                    
                    // Beep + Flash de exito
                    this.scannerBeep('success');
                    this.scannerFlash('success');
                    
                } else {
                    // Producto no encontrado
                    this.scannerFeedbackMsg(
                        'error', 
                        'Codigo no encontrado: ' + codigo
                    );
                    this.scannerBeep('error');
                    this.scannerFlash('error');
                }
                
            } catch (error) {
                console.error('Error al procesar scan:', error);
                this.scannerFeedbackMsg(
                    'error', 
                    'Error de conexion al buscar producto'
                );
                this.scannerBeep('error');
                this.scannerFlash('error');
            } finally {
                this.scannerProcessing = false;
                this.scannerState = 'ready';
                
                // Re-enfocar el scanner input para el siguiente scan
                this.$nextTick(() => {
                    this.focusScanner();
                });
            }
        },
        
        /**
         * Beep via Web Audio API (sin archivos externos)
         */
        scannerBeep(type) {
            try {
                const ctx = new (window.AudioContext || window.webkitAudioContext)();
                const osc = ctx.createOscillator();
                const gain = ctx.createGain();
                
                osc.connect(gain);
                gain.connect(ctx.destination);
                gain.gain.value = 0.3;
                
                if (type === 'success') {
                    osc.frequency.value = 1200;
                    osc.start();
                    osc.stop(ctx.currentTime + 0.1);
                } else {
                    osc.frequency.value = 400;
                    osc.start();
                    osc.stop(ctx.currentTime + 0.15);
                    
                    // Segundo beep mas grave
                    setTimeout(() => {
                        try {
                            const osc2 = ctx.createOscillator();
                            const gain2 = ctx.createGain();
                            osc2.connect(gain2);
                            gain2.connect(ctx.destination);
                            osc2.frequency.value = 300;
                            gain2.gain.value = 0.3;
                            osc2.start();
                            osc2.stop(ctx.currentTime + 0.15);
                        } catch(e) {}
                    }, 180);
                }
            } catch (e) {
                console.warn('Audio no disponible para beep');
            }
        },
        
        /**
         * Flash visual en el panel del carrito
         */
        scannerFlash(type) {
            const panel = document.querySelector('.panel-carrito');
            if (!panel) return;
            
            panel.classList.remove('scanner-flash-success', 'scanner-flash-error');
            void panel.offsetWidth; // Forzar reflow
            
            panel.classList.add(
                type === 'success' ? 'scanner-flash-success' : 'scanner-flash-error'
            );
            
            setTimeout(() => {
                panel.classList.remove('scanner-flash-success', 'scanner-flash-error');
            }, 800);
        },
        
        /**
         * Toast de notificacion
         */
        scannerFeedbackMsg(type, message) {
            if (this.scannerToastTimeout) {
                clearTimeout(this.scannerToastTimeout);
            }
            
            this.scannerToastType = type;
            this.scannerToastMessage = message;
            this.scannerToastVisible = true;
            
            this.scannerToastTimeout = setTimeout(() => {
                this.scannerToastVisible = false;
            }, type === 'error' ? 4000 : 2500);
        },
       
      

        // ============================================
        // UTILIDADES
        // ============================================
        
        /**
         * Obtiene el token CSRF para las peticiones POST
         */
        
    }
}
