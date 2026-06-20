#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Verify that every runtime ties SQLAlchemy echo to its effective log level."""

from __future__ import annotations

import importlib
from collections.abc import Callable
from typing import Any

import pytest


@pytest.mark.parametrize(
    ("module_name", "factory_args"),
    [
        ("web.database", ()),
        ("arena.database", ("postgresql+asyncpg://example",)),
        ("autojudge.db.engine", ()),
        ("rating.database", ("postgresql+asyncpg://example",)),
        ("aiassistant.database", ("postgresql+asyncpg://example",)),
    ],
)
@pytest.mark.parametrize(("log_level", "expected_echo"), [("DEBUG", True), ("INFO", False)])
def test_database_echo_follows_log_level(
    monkeypatch: pytest.MonkeyPatch,
    module_name: str,
    factory_args: tuple[object, ...],
    log_level: str,
    expected_echo: bool,
) -> None:
    """Each engine factory passes the effective DEBUG state to SQLAlchemy."""
    database_module = importlib.import_module(module_name)
    captured_options: dict[str, object] = {}
    fake_engine = object()

    def _fake_create_async_engine(*args: object, **options: object) -> object:
        captured_options.update(options)
        return fake_engine

    monkeypatch.setattr(database_module.settings, "LOG_LEVEL", log_level)
    monkeypatch.setattr(database_module, "create_async_engine", _fake_create_async_engine)

    factory: Callable[..., Any]
    if module_name == "autojudge.db.engine":
        factory = database_module.create_worker_engine
    else:
        factory = database_module.create_engine

    assert factory(*factory_args) is fake_engine
    assert captured_options["echo"] is expected_echo
