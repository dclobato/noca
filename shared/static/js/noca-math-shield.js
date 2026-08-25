// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 The NOCA Authors (see AUTHORS)
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

/**
 * Shields LaTeX math bodies from Marked before it parses Markdown, and
 * restores them afterward.
 *
 * Marked applies CommonMark's generic backslash-escape rule to LaTeX exactly
 * like any other text — `\{` becomes `{`, a matrix row's `\\` becomes `\` —
 * which corrupts the source before KaTeX ever sees it. With `breaks: true`
 * (the option `noca-markdown.js` always sets), an embedded newline also
 * becomes a `<br>`, splitting a multiline `$$...$$` body across text nodes
 * that KaTeX's `renderMathInElement` cannot bridge.
 *
 * `shieldMathSpans()` replaces each genuine `$…$`/`$$…$$` body with an
 * opaque, single-line placeholder token before Marked runs, so Marked cannot
 * misinterpret or split any part of it; `restoreMathSpans()` puts the
 * original text back once Marked has run. Restoring must happen BEFORE
 * DOMPurify sanitizes, not after — see `noca-markdown.js`'s pipeline
 * docstring for why, and for where these two calls sit in the full sequence.
 * Used exclusively by `noca-markdown.js`'s `toHtml()`.
 */
(function () {
    'use strict';

    // Guard character for math placeholder tokens: a Private-Use-Area code
    // point. It has no CommonMark/Marked significance (unlike a NUL byte,
    // which the HTML5 tokenizer replaces with U+FFFD and would not survive a
    // marked.parse()/DOMPurify.sanitize() round trip), so a token built from
    // it passes through both untouched as ordinary text.
    var MATH_TOKEN_GUARD = '';
    var MATH_TOKEN_PREFIX = MATH_TOKEN_GUARD + 'NOCA_MATH_';
    // Fallback only: real page loads always pull in markdown-directives.js
    // first, which exports the authoritative FENCE_PATTERN this reuses.
    var FALLBACK_FENCE_LINE_PATTERN = /^ {0,3}(`{3,}|~{3,})/;

    /**
     * The fence-line regex, reused from NocaMarkdownDirectives so a fenced
     * code block is recognized identically by both modules. Resolved lazily
     * (not at module-init time) since script load order is not guaranteed.
     *
     * @returns {RegExp} The fence-line pattern.
     */
    function fenceLinePattern() {
        return (window.NocaMarkdownDirectives && window.NocaMarkdownDirectives.FENCE_PATTERN)
            || FALLBACK_FENCE_LINE_PATTERN;
    }

    /**
     * Scan a segment for the first unescaped occurrence of a delimiter,
     * skipping inline code spans and escaped dollars along the way.
     *
     * Shared by the inline (`$`) and display (`$$`) close search below, so
     * both agree on what counts as "inside code" or "escaped".
     *
     * @param {string} text Full text being scanned.
     * @param {number} start Index to start scanning from.
     * @param {number} end Exclusive index to stop scanning at.
     * @param {(index: number) => boolean} isCandidate Called at each
     *     position outside code/escapes; return true to accept it as the
     *     close and stop scanning.
     * @returns {number} Index of the accepted close, or -1 if none is found.
     */
    function scanForClose(text, start, end, isCandidate) {
        var index = start;
        var ticks = 0;

        while (index < end) {
            var char = text.charAt(index);

            if (char === '`') {
                var tickEnd = index;
                while (tickEnd < end && text.charAt(tickEnd) === '`') tickEnd += 1;
                var tickCount = tickEnd - index;
                if (ticks === 0) {
                    ticks = tickCount;
                } else if (ticks === tickCount) {
                    ticks = 0;
                }
                index = tickEnd;
                continue;
            }

            if (ticks === 0 && char === '\\') {
                var slashEnd = index;
                while (slashEnd < end && text.charAt(slashEnd) === '\\') slashEnd += 1;
                var slashCount = slashEnd - index;
                if (text.charAt(slashEnd) === '$' && slashCount % 2 === 1) {
                    index = slashEnd + 1;
                    continue;
                }
                index = slashEnd;
                continue;
            }

            if (ticks === 0 && isCandidate(index)) return index;

            index += 1;
        }

        return -1;
    }

    /**
     * Find the close of a same-line inline `$…$` span.
     *
     * Requires the character right before the close to be non-whitespace
     * (and the close not to be the start of `$$`), so an unmatched prose
     * dollar such as "Price: $10" is never mistaken for math.
     *
     * @param {string} text Full text being scanned.
     * @param {number} start Index right after the opening `$`.
     * @param {number} end Exclusive index of the end of the current line.
     * @returns {number} Index of the closing `$`, or -1 if none qualifies.
     */
    function findInlineMathClose(text, start, end) {
        return scanForClose(text, start, end, function (index) {
            if (text.charAt(index) !== '$') return false;
            if (text.charAt(index + 1) === '$') return false;
            return index > start && !/\s/.test(text.charAt(index - 1));
        });
    }

    /**
     * Find the close of a `$$…$$` span, which may cross line breaks but
     * never crosses into a fenced code block.
     *
     * @param {string} text Full text being scanned.
     * @param {number} start Index right after the opening `$$`.
     * @returns {number} Index of the closing `$$`, or -1 if none is found.
     */
    function findDisplayMathClose(text, start) {
        var length = text.length;
        var searchEnd = length;
        var lineStart = start;

        while (lineStart < length) {
            var lineEnd = text.indexOf('\n', lineStart);
            if (lineEnd === -1) { lineEnd = length; }
            if (fenceLinePattern().test(text.slice(lineStart, lineEnd))) {
                searchEnd = lineStart;
                break;
            }
            lineStart = lineEnd + 1;
        }

        return scanForClose(text, start, searchEnd, function (index) {
            return text.charAt(index) === '$' && text.charAt(index + 1) === '$';
        });
    }

    /**
     * Replace every genuine `$…$`/`$$…$$` body with an opaque placeholder
     * token before Marked ever sees the Markdown source.
     *
     * A `$`/`$$` with no valid matching close (per `findInlineMathClose` /
     * `findDisplayMathClose`) is left completely untouched, so an unmatched
     * prose dollar never poisons the rest of the document. Fenced code
     * blocks and inline code spans are skipped, matching
     * `NocaMarkdownDirectives.prepareMarkdown()`'s existing fence handling.
     *
     * @param {string} rawMarkdown Raw Markdown source.
     * @returns {{markdown: string, spans: Object<string, string>}} Markdown
     *     with math bodies replaced by tokens, and the token → original-body
     *     map needed to restore them later.
     */
    function shieldMathSpans(rawMarkdown) {
        var spans = {};
        var counter = 0;
        var output = '';
        var index = 0;
        var length = rawMarkdown.length;
        var inlineTicks = 0;
        var fenceCharacter = null;
        var fenceLength = 0;
        var atLineStart = true;

        while (index < length) {
            if (atLineStart) {
                atLineStart = false;
                var lineEnd = rawMarkdown.indexOf('\n', index);
                if (lineEnd === -1) { lineEnd = length; }
                var line = rawMarkdown.slice(index, lineEnd);

                if (fenceCharacter) {
                    var closing = new RegExp(
                        '^ {0,3}' + fenceCharacter + '{' + fenceLength + ',}\\s*$'
                    );
                    if (closing.test(line)) {
                        fenceCharacter = null;
                        fenceLength = 0;
                    }
                    output += line;
                    index = lineEnd;
                    continue;
                }

                if (inlineTicks === 0) {
                    var fenceMatch = fenceLinePattern().exec(line);
                    if (fenceMatch) {
                        fenceCharacter = fenceMatch[1].charAt(0);
                        fenceLength = fenceMatch[1].length;
                        output += line;
                        index = lineEnd;
                        continue;
                    }
                }
            }

            var char = rawMarkdown.charAt(index);

            if (char === '\n') {
                output += char;
                index += 1;
                atLineStart = true;
                continue;
            }

            if (char === '`') {
                var tickEnd = index;
                while (rawMarkdown.charAt(tickEnd) === '`') tickEnd += 1;
                var tickCount = tickEnd - index;
                if (inlineTicks === 0) {
                    inlineTicks = tickCount;
                } else if (inlineTicks === tickCount) {
                    inlineTicks = 0;
                }
                output += rawMarkdown.slice(index, tickEnd);
                index = tickEnd;
                continue;
            }

            if (inlineTicks === 0 && char === '\\') {
                var slashEnd = index;
                while (rawMarkdown.charAt(slashEnd) === '\\') slashEnd += 1;
                var slashCount = slashEnd - index;
                if (rawMarkdown.charAt(slashEnd) === '$' && slashCount % 2 === 1) {
                    output += rawMarkdown.slice(index, slashEnd + 1);
                    index = slashEnd + 1;
                    continue;
                }
                output += rawMarkdown.slice(index, slashEnd);
                index = slashEnd;
                continue;
            }

            if (inlineTicks === 0 && char === '$') {
                var isDisplay = rawMarkdown.charAt(index + 1) === '$';
                var delimiter = isDisplay ? '$$' : '$';
                var bodyStart = index + delimiter.length;
                var currentLineEnd = rawMarkdown.indexOf('\n', bodyStart);
                if (currentLineEnd === -1) { currentLineEnd = length; }
                var closeIndex = isDisplay
                    ? findDisplayMathClose(rawMarkdown, bodyStart)
                    : findInlineMathClose(rawMarkdown, bodyStart, currentLineEnd);

                if (closeIndex === -1) {
                    output += delimiter;
                    index = bodyStart;
                    continue;
                }

                // Verified absent from the source, not just unique among
                // shielded spans: a deterministic, unverified token could
                // coincidentally match text the author actually typed
                // elsewhere (in prose, or inside another math body), and a
                // global restore would then corrupt that unrelated text too.
                var token = MATH_TOKEN_PREFIX + counter + MATH_TOKEN_GUARD;
                counter += 1;
                while (rawMarkdown.indexOf(token) !== -1) {
                    token = MATH_TOKEN_PREFIX + counter + MATH_TOKEN_GUARD;
                    counter += 1;
                }
                spans[token] = rawMarkdown.slice(bodyStart, closeIndex);
                output += delimiter + token + delimiter;
                index = closeIndex + delimiter.length;
                continue;
            }

            output += char;
            index += 1;
        }

        return { markdown: output, spans: spans };
    }

    /**
     * Put shielded math bodies back into Marked's HTML output.
     *
     * Must run BEFORE DOMPurify sanitizes, not after: this is a blind string
     * substitution, so a body that lands inside an HTML attribute value (e.g.
     * because the author's raw HTML contained a `$`) can otherwise splice
     * unsanitized markup into HTML DOMPurify already approved. Running before
     * sanitize means DOMPurify inspects the true final HTML and still strips
     * anything dangerous, exactly as it would for hand-authored input.
     *
     * `<`, `>`, and `&` are restored as HTML entities so the browser's HTML
     * parser turns them back into literal characters inside the text node
     * (which is what KaTeX reads); every other character needs no escaping
     * to sit safely in HTML text content. Each token is verified absent from
     * the original source before it is ever handed out (see `nextToken()`),
     * so a plain global replace cannot collide with authored text or mismatch
     * even when the same math body was authored more than once.
     *
     * @param {string} html Marked's HTML output for shielded Markdown, not
     *     yet sanitized.
     * @param {Object<string, string>} spans Token → original-body map from
     *     `shieldMathSpans()`.
     * @returns {string} HTML with every math body restored, ready to sanitize.
     */
    function restoreMathSpans(html, spans) {
        return Object.keys(spans).reduce(function (result, token) {
            var safe = spans[token]
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;');
            return result.split(token).join(safe);
        }, html);
    }

    window.NocaMathShield = Object.freeze({
        shieldMathSpans: shieldMathSpans,
        restoreMathSpans: restoreMathSpans
    });
})();
