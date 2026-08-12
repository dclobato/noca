#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

ARG ASSETS_BASE_REF=noca/assets-base:latest
ARG ASSETS_PLATFORM=linux/amd64

FROM --platform=${ASSETS_PLATFORM} ${ASSETS_BASE_REF} AS assets

FROM caddy:2.11.4-alpine

LABEL org.opencontainers.image.source="https://github.com/dclobato/noca"

COPY landingpage/Caddyfile /etc/caddy/Caddyfile
COPY landingpage/index.html /srv/index.html
COPY landingpage/static /srv/static
COPY containers/landingpage/entrypoint.sh /usr/local/bin/noca-landingpage-entrypoint

# Each linked module keeps its own portrait. Taken from the module that owns it
# rather than copied into this directory, so the landing page cannot drift away
# from the artwork those modules actually use.
COPY web/static/img/contest-256x256.png /srv/static/img/contest.png
COPY arena/static/img/logo-256x256.png /srv/static/img/arena.png
COPY animator/static/img/animator-256x256.png /srv/static/img/animator.png

# Browsers request /favicon.ico at the site root on their own, whatever the page
# declares. Without the file that request answers 404 with an HTML body, which
# Firefox reports as an OpaqueResponseBlocking error. The NOCA icon is taken from
# the module that owns it, so the front door shows the same tab icon as Contest.
COPY web/assets/favicon.ico /srv/favicon.ico

# The landing page uses the same three NOCA typefaces as Contest and Arena, so the
# front door matches the building. It takes the shared stylesheet verbatim — the
# filenames it points at carry content hashes and must not be retyped here — but
# only the three families' files. The stylesheet also declares Material Symbols
# and Font Awesome faces (12 MB combined) that this page never references, and an
# unreferenced @font-face is never fetched, so their files are left behind.
COPY --from=assets /app/shared/static/vendor/noca-fonts.css /srv/static/vendor/noca-fonts.css
COPY --from=assets /app/shared/static/webfonts/public-sans-*.woff2 /srv/static/webfonts/
COPY --from=assets /app/shared/static/webfonts/inter-*.woff2 /srv/static/webfonts/
COPY --from=assets /app/shared/static/webfonts/ibm-plex-mono-*.woff2 /srv/static/webfonts/

RUN addgroup -S caddy \
    && adduser -S -D -H -G caddy caddy \
    && chmod +x /usr/local/bin/noca-landingpage-entrypoint \
    && mkdir -p /tmp/caddy/config /tmp/caddy/data \
    && chown -R caddy:caddy /srv /tmp/caddy

ENV XDG_CONFIG_HOME=/tmp/caddy/config \
    XDG_DATA_HOME=/tmp/caddy/data

USER caddy

EXPOSE 8080

ENTRYPOINT ["/usr/local/bin/noca-landingpage-entrypoint"]
CMD ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]
