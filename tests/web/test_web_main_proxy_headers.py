"""Tests for reverse-proxy header handling at web startup."""

import web.main


def test_main_enables_proxy_headers_for_trusted_proxies(monkeypatch) -> None:
    """main() must start Uvicorn with forwarded-header support enabled."""
    captured: dict[str, object] = {}

    def _fake_run(*args: object, **kwargs: object) -> None:
        captured["args"] = args
        captured["kwargs"] = kwargs

    monkeypatch.setattr(web.main.uvicorn, "run", _fake_run)

    web.main.main()

    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["proxy_headers"] is True
    assert kwargs["forwarded_allow_ips"] == web.main.settings.FORWARDED_ALLOW_IPS
