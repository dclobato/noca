#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Serve the landing page locally, without Docker or Caddy.

The landing page has no application runtime, so there is no ``noca-landingpage``
entry point to run during development. This script stands in for Caddy: it
renders the same ``{{env "..." | html}}`` calls the Caddyfile does, serves the
same paths, and sends the same security headers, so what you see here is what
the container serves.

It is a development tool. The container runs Caddy, never this script.

Usage:
    uv run python landingpage/serve_dev.py
    uv run python landingpage/serve_dev.py --port 9000
    uv run python landingpage/serve_dev.py --host 127.0.0.1   # local only

It binds every interface by default, so the preview is reachable from another
machine on the network. It serves nothing but the public landing page.

Every ``NOCA_LANDINGPAGE_*`` variable already set in the environment is used as
is; the rest fall back to obvious placeholders so the page runs with no setup.
"""

from __future__ import annotations

import argparse
import html
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
from functools import partial
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

LANDINGPAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = LANDINGPAGE_DIR.parent
SHARED_STATIC = REPO_ROOT / "shared" / "static"

TEMPLATE_CALL = re.compile(r'\{\{env "([A-Z_]+)" \| html\}\}')

# Mirrors the Caddyfile's Content-Security-Policy and defensive headers, so a
# violation shows up here rather than after a container build.
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'self'; script-src 'self'; font-src 'self'; "
        "img-src 'self' data:; connect-src 'none'; base-uri 'none'; "
        "frame-ancestors 'none'; form-action 'none'; object-src 'none'"
    ),
    "Cache-Control": "no-store",
}

PLACEHOLDERS = {
    "NOCA_LANDINGPAGE_WEB_URL": "https://contest.example.org",
    "NOCA_LANDINGPAGE_ARENA_URL": "https://arena.example.org",
    "NOCA_LANDINGPAGE_ANIMATOR_URL": "https://scoreboard.example.org",
    "NOCA_LANDINGPAGE_HEALTHMON_URL": "https://status.example.org",
}

# The build-time assets the container takes from the assets-base stage. They are
# gitignored, so a fresh clone has to fetch them before the page renders in its
# real typefaces.
ASSET_SOURCES = ((SHARED_STATIC / "vendor" / "noca-fonts.css", Path("vendor") / "noca-fonts.css"),)
WEBFONT_PREFIXES = ("public-sans-", "inter-", "ibm-plex-mono-")

# Served from the site root, not from `static/`: browsers ask for `/favicon.ico`
# on their own regardless of what the page declares.
ROOT_ASSETS = ((REPO_ROOT / "web" / "assets" / "favicon.ico", Path("favicon.ico")),)

PORTRAITS = (
    (REPO_ROOT / "web" / "static" / "img" / "contest-256x256.png", Path("img") / "contest.png"),
    (REPO_ROOT / "arena" / "static" / "img" / "logo-256x256.png", Path("img") / "arena.png"),
    (REPO_ROOT / "animator" / "static" / "img" / "animator-256x256.png", Path("img") / "animator.png"),
)


def default_version() -> str:
    """Derive a footer version tag for local use.

    Returns:
        str: The repository's current git description, or a clear placeholder
        when git is unavailable.
    """
    try:
        described = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "describe", "--tags", "--always"],
            capture_output=True,
            check=True,
            text=True,
            timeout=5,
        )
    except OSError, subprocess.SubprocessError:
        return "dev"
    return described.stdout.strip() or "dev"


def build_environment() -> dict[str, str]:
    """Resolve the values the page template needs.

    Returns:
        dict[str, str]: Every template variable, preferring the real environment
        and falling back to development placeholders.
    """
    resolved = {name: os.environ.get(name) or fallback for name, fallback in PLACEHOLDERS.items()}
    resolved["NOCA_LANDINGPAGE_VERSION"] = os.environ.get("NOCA_LANDINGPAGE_VERSION") or default_version()
    return resolved


def stage_site(destination: Path) -> list[str]:
    """Assemble the document root Caddy would serve.

    Args:
        destination: Directory to populate. Replaced if it already exists.

    Returns:
        list[str]: Human-readable warnings about assets that could not be
        staged, so a degraded preview is never silent.
    """
    warnings: list[str] = []
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(LANDINGPAGE_DIR / "static", destination / "static")

    environment = build_environment()
    template = (LANDINGPAGE_DIR / "index.html").read_text(encoding="utf-8")
    rendered = TEMPLATE_CALL.sub(lambda match: html.escape(environment[match.group(1)]), template)
    (destination / "index.html").write_text(rendered, encoding="utf-8")

    staged_assets = [(source, destination / "static" / relative) for source, relative in (*ASSET_SOURCES, *PORTRAITS)]
    staged_assets += [(source, destination / relative) for source, relative in ROOT_ASSETS]
    for source, target in staged_assets:
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_file():
            shutil.copyfile(source, target)
        else:
            warnings.append(f"missing {source.relative_to(REPO_ROOT)}")

    webfonts_source = SHARED_STATIC / "webfonts"
    webfonts_target = destination / "static" / "webfonts"
    webfonts_target.mkdir(parents=True, exist_ok=True)
    staged = 0
    for font in sorted(webfonts_source.glob("*.woff2")) if webfonts_source.is_dir() else []:
        if font.name.startswith(WEBFONT_PREFIXES):
            shutil.copyfile(font, webfonts_target / font.name)
            staged += 1
    if not staged:
        warnings.append("no webfonts found - run: uv run python scripts/fetch_assets.py")

    return warnings


class LandingPageHandler(SimpleHTTPRequestHandler):
    """Serve the staged site with the container's headers and route rules."""

    def end_headers(self) -> None:
        """Attach the security headers before finishing the response."""
        for field, value in SECURITY_HEADERS.items():
            self.send_header(field, value)
        super().end_headers()

    def do_GET(self) -> None:  # noqa: N802 - name fixed by BaseHTTPRequestHandler
        """Answer `/health` locally and serve files for every other path."""
        if self.path == "/health":
            payload = b'{"status":"ok"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        super().do_GET()

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - signature fixed upstream
        """Keep the console to one concise line per request."""
        sys.stderr.write(f"  {self.address_string()} {format % args}\n")


def _reachable_host(bound_host: str) -> str:
    """Turn a bind address into one a browser can actually open.

    Args:
        bound_host: The address the server was asked to bind.

    Returns:
        str: `bound_host` unchanged, unless it is the wildcard, in which case
        this machine's outbound address so the printed URL works from another
        device on the network.
    """
    if bound_host not in ("0.0.0.0", ""):  # noqa: S104 - recognising the wildcard, not binding it
        return bound_host

    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Selects the outbound interface without sending anything.
        probe.connect(("192.0.2.1", 9))
        return str(probe.getsockname()[0])
    except OSError:
        return "127.0.0.1"
    finally:
        probe.close()


def main() -> int:
    """Stage the site and serve it until interrupted.

    Returns:
        int: Process exit status.
    """
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8080, help="port to listen on (default: 8080)")
    parser.add_argument(
        "--host",
        default="0.0.0.0",  # noqa: S104 - a development preview meant to be reachable from another machine
        help="address to bind (default: 0.0.0.0, every interface; pass 127.0.0.1 for local only)",
    )
    arguments = parser.parse_args()

    # Staged outside the repository: a signal that skips the cleanup below must
    # not leave an untracked copy of the site sitting in the working tree.
    with tempfile.TemporaryDirectory(prefix="noca-landingpage-") as temporary:
        staging = Path(temporary) / "site"
        for warning in stage_site(staging):
            print(f"warning: {warning}", file=sys.stderr, flush=True)

        handler = partial(LandingPageHandler, directory=str(staging))
        server = HTTPServer((arguments.host, arguments.port), handler)
        # Flushed explicitly: the server blocks immediately below, so buffered
        # output would leave a piped or redirected caller with a silent process.
        print(f"landing page preview: http://{_reachable_host(arguments.host)}:{arguments.port}/", flush=True)
        if arguments.host == "0.0.0.0":  # noqa: S104 - matches the documented default above
            print("listening on every interface; pass --host 127.0.0.1 to keep it local", flush=True)
        print("this is a development stand-in for Caddy; press Ctrl+C to stop", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped")
        finally:
            server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
