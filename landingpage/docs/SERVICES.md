# Landing page services

The landing page has one service boundary: Caddy renders and serves static
files. It doesn't connect to any other NOCA runtime.

## Caddy static renderer

`landingpage/Caddyfile` serves `landingpage/index.html` and the
`landingpage/static/` tree from `/srv` inside the container. Caddy's template
middleware reads the four `NOCA_LANDINGPAGE_*_URL` values and
`NOCA_LANDINGPAGE_VERSION` for each response and HTML-escapes them before
placing them in link attributes and the footer.

`containers/landingpage/entrypoint.sh` validates that every URL is present,
uses the `http` or `https` scheme, and contains no whitespace, and that the
version tag is present, whitespace-free, and at most 32 characters. Invalid
configuration stops startup instead of publishing broken navigation or an
unlabelled deployment.

The service runs as the unprivileged `caddy` user and keeps its writable runtime
state under `/tmp/caddy`. The source page, its assets, and the Caddy
configuration remain read-only at runtime.

## Assets

The page ships its own stylesheet and scripts and loads nothing from a network
it does not control. The container's Content-Security-Policy starts from
`default-src 'none'` and allows only same-origin styles, scripts, fonts, and
images; `connect-src 'none'` denies the page any ability to call into the
deployment it advertises. There is no live status on the page as a direct
consequence — the Health Monitor link is the answer to "is everything up?".

Typography comes from the shared NOCA font stylesheet and webfonts, copied from
the internal `assets-base` build stage rather than restated here, because the
webfont filenames carry content hashes. Only the Public Sans, Inter, and IBM
Plex Mono files are copied; the shared stylesheet's Material Symbols and Font
Awesome faces are never referenced by this page, so their files are omitted and
never fetched. Icons are authored inline SVG for the same reason.

The Contest, Arena, and Animator illustrations are copied from `web/static/img/`,
`arena/static/img/`, and `animator/static/img/` at build time so the landing page
cannot drift away from the artwork those modules use. The Health Monitor tile
still uses a drawn mark, because that module has no illustration yet; adding one
is a single `COPY` line plus the `img` reference in `index.html`.

The tab icon is served twice, from `/static/favicon.svg` and from `/favicon.ico`.
The `.ico` is the NOCA icon `web/assets/` owns, copied at build time like the
illustrations, and it exists because browsers request `/favicon.ico` at the site
root on their own whatever the page declares: without the file that request is a
404 whose HTML body Firefox reports as an `OpaqueResponseBlocking` console error.
