(function () {
    function getItems(section) {
        return Array.from(section.querySelectorAll('[data-pagination-item]'));
    }

    function setButtonState(button, disabled) {
        if (!button) return;
        button.disabled = disabled;
        button.classList.toggle('opacity-40', disabled);
        button.classList.toggle('cursor-not-allowed', disabled);
    }

    window.setupDashboardPagination = function setupDashboardPagination(root) {
        const scope = root || document;
        const sections = scope.querySelectorAll('[data-dashboard-pagination]');

        sections.forEach((section) => {
            if (section.dataset.paginationReady === '1') return;

            const items = getItems(section);
            const pageSize = Math.max(parseInt(section.dataset.pageSize || '6', 10), 1);
            const controls = section.querySelector('[data-pagination-controls]');
            const prev = section.querySelector('[data-pagination-prev]');
            const next = section.querySelector('[data-pagination-next]');
            const summary = section.querySelector('[data-pagination-summary]');
            const pageLabel = section.querySelector('[data-pagination-page]');
            let currentPage = 1;

            section.dataset.paginationReady = '1';

            function render() {
                const totalPages = Math.max(Math.ceil(items.length / pageSize), 1);
                currentPage = Math.min(Math.max(currentPage, 1), totalPages);

                const start = (currentPage - 1) * pageSize;
                const end = start + pageSize;

                items.forEach((item, index) => {
                    item.style.display = index >= start && index < end ? '' : 'none';
                });

                if (controls) {
                    controls.classList.toggle('hidden', items.length <= pageSize);
                }

                if (summary && items.length > 0) {
                    summary.textContent = `Mostrando ${start + 1}-${Math.min(end, items.length)} de ${items.length}`;
                }

                if (pageLabel) {
                    pageLabel.textContent = `${currentPage} / ${totalPages}`;
                }

                setButtonState(prev, currentPage <= 1);
                setButtonState(next, currentPage >= totalPages);
            }

            if (prev) {
                prev.addEventListener('click', () => {
                    currentPage -= 1;
                    render();
                });
            }

            if (next) {
                next.addEventListener('click', () => {
                    currentPage += 1;
                    render();
                });
            }

            render();
        });
    };
})();
