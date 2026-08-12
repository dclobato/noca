# Development Environment Setup

The local development workflow now lives in [BOOTSTRAP.md](./BOOTSTRAP.md).

Use the `Local development` quick start and the `Local Development Bootstrap`
sections there as the maintained source for host-run setup of all workspace
modules: `web`, `arena`, `autojudge`, `rating`, `aiassistant`, `healthmonitor`,
and `animator`. The non-Python `landingpage` module runs through its dedicated
Caddy container; for local work, `uv run python landingpage/serve_dev.py` serves
the page with the container's own headers and no Docker. See its
[module README](../landingpage/README.md).
