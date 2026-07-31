#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the health monitor's monitored-service registry.

The registry is the single source of truth for which runtimes the monitor
watches. Its most important property is completeness: a runtime that publishes
Valkey presence but is missing here is silently unmonitored, which looks exactly
like a healthy system.
"""

from __future__ import annotations

from healthmonitor.services.service_registry import MONITORED_SERVICES
from shared.services.valkey_service import WorkerClass


def test_registry_covers_every_worker_class() -> None:
    """Every presence-publishing class is monitored, so none is silently ignored."""
    registered = {service.worker_class for service in MONITORED_SERVICES}
    assert registered == set(WorkerClass)


def test_registry_has_no_duplicate_entries() -> None:
    """Each worker class appears exactly once, so no service renders twice."""
    classes = [service.worker_class for service in MONITORED_SERVICES]
    assert len(classes) == len(set(classes))


def test_animator_is_registered_with_presentation_metadata() -> None:
    """The animator is monitored and carries a usable title and icon."""
    by_class = {service.worker_class: service for service in MONITORED_SERVICES}
    animator = by_class[WorkerClass.ANIMATOR]
    assert animator.title == "Animator"
    assert animator.icon


def test_titles_and_icons_are_unique() -> None:
    """Distinct services stay visually distinguishable on both dashboards."""
    titles = [service.title for service in MONITORED_SERVICES]
    icons = [service.icon for service in MONITORED_SERVICES]
    assert len(titles) == len(set(titles))
    assert len(icons) == len(set(icons))


def test_display_order_is_stable() -> None:
    """Card order is fixed, so the dashboards do not reshuffle between requests."""
    assert [service.worker_class for service in MONITORED_SERVICES] == [
        WorkerClass.WEB,
        WorkerClass.ARENA,
        WorkerClass.AUTOJUDGE,
        WorkerClass.RATING,
        WorkerClass.AIASSISTANT,
        WorkerClass.ANIMATOR,
    ]
