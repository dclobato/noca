// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 The NOCA Authors (see AUTHORS)
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

/**
 * The single Markdown rendering pipeline for every NOCA surface.
 *
 * Pipeline, in order:
 *   0. NocaMathShield.shieldMathSpans()         — hide LaTeX bodies from Marked
 *   1. NocaMarkdownDirectives.prepareMarkdown() — NOCA directives, literal `\$`
 *   2. marked.parse()                          — Markdown → HTML
 *   3. NocaMathShield.restoreMathSpans()        — put LaTeX bodies back
 *   4. DOMPurify.sanitize()                    — strip unsafe HTML
 *   5. DOM injection
 *   6. Mermaid                                 — promote and run fenced diagrams
 *   7. KaTeX renderMathInElement()             — inline/display math
 *   8. NocaMarkdownDirectives.apply()          — directive paragraphs → classes
 *
 * Steps 0 and 3 (see `noca-math-shield.js`) exist because Marked applies
 * CommonMark's generic backslash-escape rule to LaTeX exactly like any other
 * text (`\{` becomes `{`, a matrix row's `\\` becomes `\`), which corrupts
 * the source before KaTeX ever sees it. `shieldMathSpans()` replaces each
 * `$…$`/`$$…$$` body with an opaque, single-line placeholder token before
 * Marked runs, so Marked cannot misinterpret any character inside it (and,
 * with `breaks: true`, cannot split a multiline body across a `<br>`).
 * Restoring happens BEFORE sanitizing, not after: `restoreMathSpans()` is a
 * blind string substitution that can land inside an HTML attribute value
 * (e.g. raw HTML the author wrote containing a `$`, such as
 * `title="$x" onmouseover="alert(1)$"`), so it must run before DOMPurify has
 * the chance to inspect and strip whatever that substitution introduces —
 * restoring after sanitizing would splice unsanitized markup into HTML
 * DOMPurify already approved, defeating it entirely.
 *
 * Pages bind declaratively; no page ships its own copy of this pipeline:
 *
 *   External source (entity-escaped inside a `text/plain` script blob):
 *     <div class="noca-markdown" data-noca-markdown="statement-src"></div>
 *     <script id="statement-src" type="text/plain">{{ value | e }}</script>
 *
 *   In place (the element already holds its own decoded source text):
 *     <div class="noca-markdown" data-noca-markdown>{{ value | e }}</div>
 *
 * Every optional dependency is feature-detected, so a page that omits Mermaid
 * or KaTeX still renders Markdown rather than failing. `noca-math-shield.js`
 * is required, not optional — every page that loads this module must load it
 * first (see `docs/SHARED_SERVICES.md`); its absence degrades `toHtml()` back
 * to the pre-shielding, escape-vulnerable behavior rather than failing.
 */
(function () {
    'use strict';

    var BINDING_SELECTOR = '[data-noca-markdown]';
    // Math-only surfaces: pages that author LaTeX directly in HTML rather than
    // through Markdown still get their KaTeX pass from this module, so the
    // delimiters and ignored tags are defined exactly once.
    var MATH_SELECTOR = '[data-noca-math]';
    var MERMAID_CODE_SELECTOR = 'pre > code.language-mermaid, pre > code.lang-mermaid';
    var KATEX_OPTIONS = {
        delimiters: [
            { left: '$$', right: '$$', display: true },
            { left: '$', right: '$', display: false },
            { left: '\\(', right: '\\)', display: false },
            { left: '\\[', right: '\\]', display: true }
        ],
        throwOnError: false,
        ignoredTags: ['script', 'noscript', 'style', 'textarea', 'pre', 'code', 'svg']
    };

    var mermaidReady = false;

    /**
     * Decode HTML entities carried by a `text/plain` script blob.
     *
     * The HTML parser leaves script content escaped, so `{{ value | e }}` must be
     * decoded before Marked sees it. Text held directly by a rendered element is
     * already decoded by the parser and must not pass through here.
     *
     * @param {string} text Escaped text.
     * @returns {string} Decoded text.
     */
    function decodeHtmlEntities(text) {
        var textarea = document.createElement('textarea');
        textarea.innerHTML = text;
        return textarea.value;
    }

    /**
     * Convert Markdown source to sanitized HTML.
     *
     * @param {string} rawMarkdown Raw Markdown source.
     * @returns {string} Sanitized HTML, or an empty string when Marked is absent.
     */
    function toHtml(rawMarkdown) {
        if (typeof marked === 'undefined') return '';

        marked.setOptions({ breaks: true, gfm: true });

        var shielded = window.NocaMathShield
            ? window.NocaMathShield.shieldMathSpans(rawMarkdown)
            : { markdown: rawMarkdown, spans: {} };
        var prepared = window.NocaMarkdownDirectives
            ? window.NocaMarkdownDirectives.prepareMarkdown(shielded.markdown)
            : shielded.markdown;
        var html = marked.parse(prepared);
        // Restore BEFORE sanitizing, not after: restoration is a blind string
        // substitution that can land inside an attribute value (e.g. a `$`
        // inside raw HTML the author wrote, such as `title="$x"
        // onmouseover="alert(1)$"`), which would otherwise splice unsanitized
        // markup into HTML DOMPurify already approved. Sanitizing last means
        // DOMPurify inspects the actual final HTML — including anything a
        // restored body introduces — and strips anything dangerous exactly as
        // it would for hand-authored input.
        var restored = window.NocaMathShield
            ? window.NocaMathShield.restoreMathSpans(html, shielded.spans)
            : html;

        return typeof DOMPurify !== 'undefined' ? DOMPurify.sanitize(restored) : restored;
    }

    /**
     * Replace fenced `mermaid` code blocks with the elements Mermaid expects.
     *
     * @param {Element} container Rendered Markdown container.
     * @returns {void}
     */
    function promoteMermaidBlocks(container) {
        container.querySelectorAll(MERMAID_CODE_SELECTOR).forEach(function (code) {
            var block = document.createElement('pre');
            block.className = 'mermaid';
            block.textContent = (code.textContent || '')
                .replace(/\r\n/g, '\n')
                .replace(/\r/g, '\n')
                .trim();
            code.parentElement.replaceWith(block);
        });
    }

    /**
     * Render any Mermaid diagrams inside a container.
     *
     * @param {Element} container Rendered Markdown container.
     * @returns {Promise<void>} Resolves once Mermaid has run.
     */
    async function runMermaid(container) {
        promoteMermaidBlocks(container);

        if (typeof mermaid === 'undefined') return;

        var nodes = container.querySelectorAll('.mermaid');
        if (!nodes.length) return;

        if (!mermaidReady) {
            mermaid.initialize({ startOnLoad: false, securityLevel: 'strict' });
            mermaidReady = true;
        }

        try {
            await mermaid.run({ nodes: nodes, suppressErrors: false });
        } catch (err) {
            console.error('Mermaid render failed:', err);
        }
    }

    /**
     * Apply post-parse passes to already-injected Markdown HTML.
     *
     * KaTeX runs on the container itself: `pre`, `code`, and `svg` are ignored
     * tags, so code blocks and rendered Mermaid diagrams are skipped without
     * needing to walk children individually.
     *
     * @param {Element} container Element holding rendered Markdown HTML.
     * @returns {Promise<void>} Resolves once every pass has run.
     */
    async function enhance(container) {
        await runMermaid(container);
        renderMath(container);

        if (window.NocaMarkdownDirectives) {
            window.NocaMarkdownDirectives.apply(container);
        }
    }

    /**
     * Run the KaTeX pass over a container, using the shared delimiters.
     *
     * Public so a page that authors LaTeX directly in HTML (no Markdown) shares
     * this configuration instead of repeating it.
     *
     * @param {Element} container Element that may contain LaTeX.
     * @returns {void}
     */
    function renderMath(container) {
        if (typeof renderMathInElement !== 'function') return;
        renderMathInElement(container, KATEX_OPTIONS);
    }

    /**
     * Read the Markdown source a bound container declares.
     *
     * @param {Element} container Element carrying `data-noca-markdown`.
     * @returns {string} Raw Markdown source.
     */
    function readSource(container) {
        var sourceId = container.getAttribute('data-noca-markdown');

        if (!sourceId) return container.textContent || '';

        var source = document.getElementById(sourceId);
        return source ? decodeHtmlEntities(source.textContent || '') : '';
    }

    /**
     * Render one bound container through the full pipeline.
     *
     * @param {Element} container Element carrying `data-noca-markdown`.
     * @returns {Promise<void>} Resolves once the container is fully rendered.
     */
    async function render(container) {
        // Without Marked there is nothing to render. Returning early matters for
        // in-place containers, whose own text is the source: blanking them would
        // destroy the content instead of leaving it as readable plain text.
        if (typeof marked === 'undefined') return;

        container.innerHTML = toHtml(readSource(container));
        await enhance(container);
    }

    /**
     * Render every bound container at or below a root.
     *
     * @param {Document|Element} [root] Search root; defaults to the document.
     * @returns {Promise<void>} Resolves once every container is rendered.
     */
    async function renderAll(root) {
        var scope = root || document;
        var isElement = scope.nodeType === Node.ELEMENT_NODE;
        var containers = Array.from(scope.querySelectorAll(BINDING_SELECTOR));

        if (isElement && scope.matches(BINDING_SELECTOR)) {
            containers.unshift(scope);
        }

        for (var index = 0; index < containers.length; index += 1) {
            await render(containers[index]);
        }

        var mathOnly = Array.from(scope.querySelectorAll(MATH_SELECTOR));
        if (isElement && scope.matches(MATH_SELECTOR)) {
            mathOnly.unshift(scope);
        }
        mathOnly.forEach(renderMath);
    }

    window.NocaMarkdown = Object.freeze({
        enhance: enhance,
        render: render,
        renderAll: renderAll,
        renderMath: renderMath,
        toHtml: toHtml
    });

    // Wait for DOMContentLoaded rather than for `readyState !== 'loading'`. This
    // module is deferred from `<head>`, while a page's vendor libraries are
    // commonly deferred at the end of `<body>`: deferred scripts run in document
    // order while `readyState` is already 'interactive', so running here would
    // find Marked undefined and render nothing. DOMContentLoaded fires only once
    // every deferred script has executed.
    if (document.readyState === 'complete') {
        renderAll();
    } else {
        document.addEventListener('DOMContentLoaded', function () { renderAll(); }, { once: true });
    }
})();
