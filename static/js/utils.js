/**
 * utils.js — Utilidades globales del sistema POS FIFO
 * Cargado en base.html, disponible en todos los templates.
 */

// ============================================
// CSRF TOKEN
// ============================================

function getCsrfToken() {
    // 1. Input hidden del form (más confiable)
    const input = document.querySelector('[name=csrfmiddlewaretoken]');
    if (input) return input.value;

    // 2. Cookie (fallback para páginas sin form)
    const match = document.cookie.match(/csrftoken=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : '';
}

/**
 * Headers estándar para fetch POST/PUT/DELETE
 * Uso: fetch(url, { method: 'POST', headers: jsonHeaders(), body: ... })
 */
function jsonHeaders() {
    return {
        'Content-Type': 'application/json',
        'X-CSRFToken': getCsrfToken(),
    };
}


// ============================================
// TOAST NOTIFICATIONS
// ============================================

/**
 * Muestra un toast no-bloqueante.
 * Reemplaza todos los alert() del sistema.
 *
 * @param {'success'|'error'|'warning'|'info'} type
 * @param {string} message
 * @param {number} duration — ms, default 4000
 */
function showToast(type, message, duration = 4000) {
    const container = document.getElementById('toast-container');
    if (!container) return;

    const icons = {
        success: '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"/>',
        error:   '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2m7-2a9 9 0 11-18 0 9 9 0 0118 0z"/>',
        warning: '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/>',
        info:    '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/>',
    };

    const colors = {
        success: { bg: 'bg-success-50', border: 'border-success-200', icon: 'text-success-600' },
        error:   { bg: 'bg-danger-50',  border: 'border-danger-200',  icon: 'text-danger-600'  },
        warning: { bg: 'bg-warning-50', border: 'border-warning-200', icon: 'text-warning-600' },
        info:    { bg: 'bg-info-50',    border: 'border-info-200',    icon: 'text-info-600'    },
    };

    const c = colors[type] || colors.info;

    const toast = document.createElement('div');
    toast.className = `flex items-start space-x-3 p-4 rounded-lg border ${c.bg} ${c.border}`;
    toast.style.cssText = 'box-shadow:0 10px 25px rgba(0,0,0,0.15);transition:all 0.3s ease;opacity:0;transform:translateY(-10px);';
    toast.style.pointerEvents = 'auto';
    toast.innerHTML = `
        <svg class="w-5 h-5 flex-shrink-0 ${c.icon}" fill="none" stroke="currentColor" viewBox="0 0 24 24">${icons[type] || icons.info}</svg>
        <span class="text-sm font-medium flex-1">${message}</span>
        <button onclick="this.parentElement.remove()" class="text-gray-400 hover:text-gray-600 flex-shrink-0">
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>
            </svg>
        </button>
    `;

    container.appendChild(toast);

    // Animate in
    requestAnimationFrame(() => {
        toast.style.opacity = '1';
        toast.style.transform = 'translateY(0)';
    });

    // Auto-dismiss
    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateY(-10px)';
        setTimeout(() => toast.remove(), 300);
    }, duration);
}



// ============================================
// CONFIRM DIALOG (reemplaza confirm() nativo)
// ============================================

/**
 * Muestra un dialogo de confirmacion estilizado.
 * Retorna una Promise que resuelve true/false.
 *
 * Uso:
 *   const ok = await showConfirm('¿Cerrar turno?', 'Esta accion no se puede deshacer.');
 *   if (ok) { ... }
 *
 * @param {string} titulo
 * @param {string} mensaje
 * @param {Object} opts
 * @param {string} opts.confirmText  — texto del boton (default 'Confirmar')
 * @param {string} opts.cancelText   — texto cancel (default 'Cancelar')
 * @param {'danger'|'warning'|'info'} opts.type — color del boton confirm
 */
function showConfirm(titulo, mensaje = '', opts = {}) {
    const {
        confirmText = 'Confirmar',
        cancelText = 'Cancelar',
        type = 'danger'
    } = opts;

    const btnColors = {
        danger:  'background:#DC2626;color:#fff;',
        warning: 'background:#D97706;color:#fff;',
        info:    'background:#2563EB;color:#fff;',
    };

    return new Promise((resolve) => {
        const overlay = document.createElement('div');
        overlay.style.cssText = 'position:fixed;inset:0;z-index:999999;display:flex;align-items:center;justify-content:center;padding:1rem;';

        const backdrop = document.createElement('div');
        backdrop.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.5);backdrop-filter:blur(4px);transition:opacity 0.2s;opacity:0;';

        const card = document.createElement('div');
        card.style.cssText = 'position:relative;background:#fff;border-radius:1rem;padding:1.5rem;max-width:400px;width:100%;box-shadow:0 25px 60px rgba(0,0,0,0.3);border:1px solid #e5e7eb;transition:all 0.2s;opacity:0;transform:scale(0.95);';

        card.innerHTML = `
            <h3 style="font-size:1.1rem;font-weight:600;color:#111827;margin:0 0 0.5rem;">${titulo}</h3>
            ${mensaje ? `<p style="font-size:0.875rem;color:#6b7280;margin:0 0 1.25rem;line-height:1.5;">${mensaje}</p>` : '<div style="margin-bottom:1rem;"></div>'}
            <div style="display:flex;justify-content:flex-end;gap:0.75rem;">
                <button id="_confirm_cancel" style="padding:0.5rem 1rem;border-radius:0.5rem;font-size:0.875rem;font-weight:500;border:1px solid #d1d5db;background:#fff;color:#374151;cursor:pointer;">${cancelText}</button>
                <button id="_confirm_ok" style="padding:0.5rem 1rem;border-radius:0.5rem;font-size:0.875rem;font-weight:500;border:none;cursor:pointer;${btnColors[type] || btnColors.danger}">${confirmText}</button>
            </div>
        `;

        overlay.appendChild(backdrop);
        overlay.appendChild(card);
        document.body.appendChild(overlay);

        // Animate in
        requestAnimationFrame(() => {
            backdrop.style.opacity = '1';
            card.style.opacity = '1';
            card.style.transform = 'scale(1)';
        });

        function close(result) {
            backdrop.style.opacity = '0';
            card.style.opacity = '0';
            card.style.transform = 'scale(0.95)';
            setTimeout(() => {
                overlay.remove();
                resolve(result);
            }, 200);
        }

        card.querySelector('#_confirm_ok').addEventListener('click', () => close(true));
        card.querySelector('#_confirm_cancel').addEventListener('click', () => close(false));
        backdrop.addEventListener('click', () => close(false));

        // ESC para cancelar
        function onKey(e) {
            if (e.key === 'Escape') {
                document.removeEventListener('keydown', onKey);
                close(false);
            }
        }
        document.addEventListener('keydown', onKey);
    });
}

