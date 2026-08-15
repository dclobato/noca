// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 The NOCA Authors (see AUTHORS)
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

document.addEventListener('DOMContentLoaded', function () {
    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    function highlightTarget() {
        if (!window.location.hash) return;

        const rawHash = window.location.hash.substring(1);
        const targetId = decodeURIComponent(rawHash);
        const targetElement = document.getElementById(targetId);

        if (!targetElement) return;

        // Verifica se o elemento está dentro de um accordion colapsado
        const accordionCollapse = targetElement.closest('.accordion-collapse');
        if (accordionCollapse && !accordionCollapse.classList.contains('show')) {
            // Expande o accordion
            const bsCollapse = new bootstrap.Collapse(accordionCollapse, {
                toggle: false
            });
            bsCollapse.show();

            // Aguarda a animação do accordion terminar antes de rolar
            accordionCollapse.addEventListener('shown.bs.collapse', function () {
                executeHighlight(targetElement);
            }, { once: true });
        } else {
            // Se já estiver visível ou não estiver em accordion, destaca imediatamente
            executeHighlight(targetElement);
        }
    }

    function executeHighlight(targetElement) {
        const tableRow = targetElement.closest('tr');
        const listItem = targetElement.closest('li');
        const highlightElements = tableRow
            ? Array.from(tableRow.querySelectorAll('td'))
            : (listItem ? [listItem] : []);
        const scrollTarget = tableRow || listItem;

        if (!scrollTarget || highlightElements.length === 0) return;

        highlightElements.forEach(element => {
            element.classList.remove('noca-row-highlight-fade');
            element.classList.add('noca-row-highlight');
        });

        scrollTarget.scrollIntoView({behavior: reduceMotion ? 'auto' : 'smooth', block: 'center'});

        setTimeout(function () {
            highlightElements.forEach(element => {
                element.classList.add('noca-row-highlight-fade');
            });

            setTimeout(function () {
                highlightElements.forEach(element => {
                    element.classList.remove('noca-row-highlight', 'noca-row-highlight-fade');
                });
            }, reduceMotion ? 0 : 2100);
        }, reduceMotion ? 1500 : 500);
    }

    // Executa no carregamento inicial
    setTimeout(highlightTarget, 300);

    // Executa quando o hash da URL muda
    window.addEventListener('hashchange', function () {
        setTimeout(highlightTarget, 300);
    });
});
