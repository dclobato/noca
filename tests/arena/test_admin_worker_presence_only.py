#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Presence-only worker classes must never reach the Arena admin dashboard.

``web``, ``arena``, and ``animator`` publish Valkey presence purely so the health
monitor can probe them. They are HTTP front doors, not queue consumers: giving
any of them a dashboard card would imply queue depth it does not have, and giving
one a pause control would offer an operator a button that cannot work.

The Arena dashboard is an explicit allowlist rather than a denylist, so these
assertions pin that design decision instead of merely restating today's contents.
"""

from __future__ import annotations

import pytest

from arena.services.admin_worker_service import (
    _CARD_METADATA,
    DASHBOARD_CLASSES,
    PAUSABLE_CLASSES,
    TRIGGER_CLASSES,
)
from shared.services.valkey_service import WorkerClass

PRESENCE_ONLY_CLASSES = (WorkerClass.WEB, WorkerClass.ARENA, WorkerClass.ANIMATOR)


@pytest.mark.parametrize("worker_class", PRESENCE_ONLY_CLASSES)
def test_presence_only_classes_have_no_dashboard_card(worker_class: WorkerClass) -> None:
    """A presence-only class renders no worker card."""
    assert worker_class not in DASHBOARD_CLASSES
    assert worker_class not in _CARD_METADATA


@pytest.mark.parametrize("worker_class", PRESENCE_ONLY_CLASSES)
def test_presence_only_classes_are_not_pausable(worker_class: WorkerClass) -> None:
    """A presence-only class exposes neither pause/resume nor one-shot triggers."""
    assert worker_class not in PAUSABLE_CLASSES
    assert worker_class not in TRIGGER_CLASSES


def test_every_dashboard_class_has_card_metadata() -> None:
    """Any class the dashboard shows can actually be rendered."""
    for worker_class in DASHBOARD_CLASSES:
        assert worker_class in _CARD_METADATA


def test_pausable_and_trigger_classes_are_dashboard_classes() -> None:
    """Controls are only ever offered for classes the dashboard actually shows."""
    assert set(PAUSABLE_CLASSES) <= set(DASHBOARD_CLASSES)
    assert set(TRIGGER_CLASSES) <= set(DASHBOARD_CLASSES)
