# Landing page URL reference

Use these URLs when configuring probes or linking to the standalone landing
page. The sample Compose stack publishes the internal port `8080` on host port
`84`.

| Name | URL | Notes |
| --- | --- | --- |
| Landing page | `/` | Public environment overview and application links. |
| Health check | `/health` | Dependency-free liveness response. |
| Static assets | `/static/…` | Stylesheet, scripts, illustrations, webfonts, and the site icon. |
