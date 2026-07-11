#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""
Per-container isolate box-id allocation.

Why this exists
---------------
Every run container is created with ``cgroupns="host"`` and the host
``/sys/fs/cgroup`` bind-mounted read-write (see ``container_pool.py``). That
means an isolate ``--box-id=N`` resolves to the *same* physical cgroup
``/sys/fs/cgroup/box-N`` across every container on the host. When all
containers shared the hardcoded ``--box-id=0``, concurrent isolate ``--init`` /
``--cleanup`` operations raced on that single cgroup and failed with
``Cannot remove control group /sys/fs/cgroup/box-0: Device or resource busy``
(EBUSY) or ``... No such file or directory`` (ENOENT).

isolate's intended mechanism for concurrent sandboxes is distinct
``--box-id`` values (each gets its own ``box-N`` cgroup). This registry hands
each container a unique box-id for its entire lifetime — allocated when the
container is created, released when it is destroyed — so two live containers
never share a box-id and therefore never collide on the shared host cgroup.

Scope and thread-safety
-----------------------
The registry is a process-global singleton guarded by a ``threading.Lock``
because it is accessed from the Docker ``ThreadPoolExecutor`` threads (container
creation, run phase, destruction), not from the asyncio event loop.

The valid box-id range is ``0 .. ISOLATE_MAX_BOXES - 1``, matching isolate's
configured ``num_boxes``. This assumes a single autojudge worker process per
host (the standard deployment: one worker owns the host Docker daemon). Running
multiple worker processes that share one host cgroupfs would require host-global
coordination instead of this per-process free list.
"""

import logging
import threading

from autojudge.config import settings

logger = logging.getLogger(__name__)


class BoxExhaustedError(Exception):
    """Raised when no isolate box-id is free within the configured range."""


class BoxRegistry:
    """Thread-safe allocator mapping container id → isolate box-id."""

    def __init__(self, max_boxes: int) -> None:
        self._lock = threading.Lock()
        self._by_container: dict[str, int] = {}
        # Free ids kept as a stack; ids reused only after the container that
        # held them (and its isolate box) has been destroyed.
        self._free: list[int] = list(range(max_boxes))
        self._max_boxes = max_boxes

    def allocate(self, container_id: str) -> int:
        """
        Reserve a unique box-id for ``container_id``.

        Idempotent: a second call for the same container returns the box-id
        already assigned to it.

        Raises:
            BoxExhaustedError: If every box-id in the range is currently in use.
        """
        with self._lock:
            existing = self._by_container.get(container_id)
            if existing is not None:
                return existing
            if not self._free:
                raise BoxExhaustedError(
                    f"No free isolate box-id (all {self._max_boxes} in use). "
                    "Increase NOCA_JUDGE_ISOLATE_MAX_BOXES or reduce pool sizes."
                )
            box_id = self._free.pop()
            self._by_container[container_id] = box_id
            return box_id

    def get(self, container_id: str) -> int:
        """
        Return the box-id assigned to ``container_id``.

        Raises:
            KeyError: If the container has no box-id (it was never a pool
                container, or was already released).
        """
        with self._lock:
            try:
                return self._by_container[container_id]
            except KeyError as exc:
                raise KeyError(f"No isolate box-id registered for container '{container_id[:12]}'") from exc

    def release(self, container_id: str) -> None:
        """
        Free the box-id held by ``container_id``, if any.

        Idempotent and safe for unknown container ids, so it can be called
        unconditionally from every container-destruction path.
        """
        with self._lock:
            box_id = self._by_container.pop(container_id, None)
            if box_id is not None:
                self._free.append(box_id)


# Process-global singleton. Every ContainerPool in this worker shares it so
# box-ids stay unique across all languages on the host.
registry = BoxRegistry(settings.ISOLATE_MAX_BOXES)
