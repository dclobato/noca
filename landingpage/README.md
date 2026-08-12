# NOCA landing page

The `landingpage` module is a standalone, static entry point for a complete NOCA
deployment. A dedicated Caddy process serves the page and injects the public
application URLs and the release tag from its runtime environment. It doesn't
use Python, Node.js, PostgreSQL, or Valkey.

The page opens with what NOCA is and the Contest/Arena split, then the four
linked instances, then how a submission becomes a verdict, the workers behind
both products, and a side-by-side of what each product does. All of its content
comes from `README.md` and `docs/`; it makes no claim those don't support.

## Configuration

Set all four public application URLs and the release tag before starting the
container:

- `NOCA_LANDINGPAGE_WEB_URL`
- `NOCA_LANDINGPAGE_ARENA_URL`
- `NOCA_LANDINGPAGE_ANIMATOR_URL`
- `NOCA_LANDINGPAGE_HEALTHMON_URL`
- `NOCA_LANDINGPAGE_VERSION`

Each URL must be an absolute `http://` or `https://` URL. The version tag is
shown in the footer next to the GitHub link; it must be non-empty, contain no
whitespace, and be at most 32 characters. The optional `NOCA_LANDINGPAGE_PORT`
variable controls Caddy's internal listener and defaults to `8080`.

Because there is no application behind the page, the version is configuration
rather than something the runtime can discover. Pass the tag you deployed.

## Running it

There is no `noca-landingpage` entry point, because there is no application to
run. Two ways to see the page:

**During development, without Docker.** `landingpage/serve_dev.py` stands in for
Caddy: it renders the same template calls, serves the same paths, and sends the
same security headers, so a CSP violation shows up before a container build.

```bash
uv run python landingpage/serve_dev.py                    # port 8080, every interface
uv run python landingpage/serve_dev.py --host 127.0.0.1   # local only
```

It binds `0.0.0.0` by default so the preview is reachable from another machine,
and prints a URL you can open from there. It serves nothing but the public
landing page.

It needs no configuration. Any `NOCA_LANDINGPAGE_*` variable already in your
environment is used as is; the rest fall back to placeholder URLs, and the
footer shows `git describe`. Run `uv run python scripts/fetch_assets.py` first,
or it warns and falls back to system fonts. This script is a development tool
and is never copied into the image.

**The real thing.** Build the image, then start the Compose service:

```bash
./containers/build.sh landingpage
docker compose -f docker-compose.yml.sample up landingpage   # http://localhost:84/
```

The build script builds the `app-base` → `assets-base` prerequisites first, so
the first run takes a few minutes. Set the four URLs and the version tag in
`.env` before starting, or the container refuses to boot.

## Testing

`tests/landingpage/test_deployment_wiring.py` covers the module's contract
without Docker: entrypoint validation, the template's variables and module
names, the absence of inline style and script, the CSP directives, the
non-overlapping cache matchers, the image's asset sources, and the build and
publish inventories.

```bash
uv run pytest tests/landingpage
```

## Container

Build the image through the repository build script:

```bash
./containers/build.sh landingpage
```

The build pulls the shared NOCA webfonts from the internal `assets-base` stage,
so the landing page uses the same typefaces as Contest and Arena. The sample
Compose stack publishes the page on host port `84`. Its `/health` endpoint
returns a small JSON response without contacting any other NOCA module.

## Boundaries

The landing page links to the user-facing Web, Arena, Animator, and Health
Monitor deployments. It identifies itself as the current entry point and names
AutoJudge, Rating, AI Assistant, and shared services as parts of the
environment, but it doesn't expose links for those non-user-facing modules.

It shows no live service status, and cannot: its Content-Security-Policy sets
`connect-src 'none'`, so the page has no way to call into the deployment it
advertises. The Health Monitor link covers that need.

See [Routes](docs/ROUTES.md), [URL reference](docs/URL_FOR_REFERENCE.md), and
[services](docs/SERVICES.md) for the runtime contract.
