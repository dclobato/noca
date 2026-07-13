#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Docker SDK bidirectional exec-socket adapter for the interactive bridge."""

from __future__ import annotations

import asyncio
import socket
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from typing import Any

import docker


class DockerExecEndpoint:
    """Expose one attached Docker exec as an `InteractiveEndpoint`."""

    def __init__(
        self,
        *,
        docker_client: docker.DockerClient,
        container_id: str,
        exec_id: str,
        raw_socket: socket.socket,
        executor: ThreadPoolExecutor,
    ) -> None:
        self._client = docker_client
        self._container_id = container_id
        self._exec_id = exec_id
        self._socket = raw_socket
        self._executor = executor
        self._stdout: asyncio.Queue[bytes] = asyncio.Queue()
        self._stderr: asyncio.Queue[bytes] = asyncio.Queue()
        self._stdout_remainder = b""
        self._stderr_remainder = b""
        self._reader_task = asyncio.create_task(self._read_frames())

    @classmethod
    async def start(
        cls,
        *,
        docker_client: docker.DockerClient,
        container_id: str,
        command: list[str],
        executor: ThreadPoolExecutor,
        user: str = "root",
    ) -> DockerExecEndpoint:
        """Create and attach a non-TTY exec with stdin and multiplexed output."""
        loop = asyncio.get_running_loop()
        created = await loop.run_in_executor(
            executor,
            lambda: docker_client.api.exec_create(
                container=container_id,
                cmd=command,
                stdin=True,
                stdout=True,
                stderr=True,
                tty=False,
                user=user,
            ),
        )
        exec_id = str(created["Id"])
        attached = await loop.run_in_executor(
            executor,
            lambda: docker_client.api.exec_start(exec_id, detach=False, tty=False, socket=True),
        )
        raw_socket = getattr(attached, "_sock", attached)
        return cls(
            docker_client=docker_client,
            container_id=container_id,
            exec_id=exec_id,
            raw_socket=raw_socket,
            executor=executor,
        )

    async def _read_frames(self) -> None:
        """Demultiplex Docker's eight-byte stream headers into async queues."""
        loop = asyncio.get_running_loop()
        try:
            while True:
                header = await loop.run_in_executor(self._executor, self._recv_exact, 8)
                if not header:
                    break
                size = int.from_bytes(header[4:8], "big")
                payload = await loop.run_in_executor(self._executor, self._recv_exact, size)
                if len(payload) != size:
                    break
                await (self._stderr if header[0] == 2 else self._stdout).put(payload)
        finally:
            await self._stdout.put(b"")
            await self._stderr.put(b"")

    def _recv_exact(self, size: int) -> bytes:
        """Read exactly `size` socket bytes, returning partial data at EOF."""
        chunks = bytearray()
        while len(chunks) < size:
            chunk = self._socket.recv(size - len(chunks))
            if not chunk:
                break
            chunks.extend(chunk)
        return bytes(chunks)

    async def read_stdout(self, size: int) -> bytes:
        """Read the next stdout frame, bounded to the requested size."""
        chunk, self._stdout_remainder = await self._read_bounded(
            self._stdout,
            self._stdout_remainder,
            size,
        )
        return chunk

    async def read_stderr(self, size: int) -> bytes:
        """Read the next stderr frame, bounded to the requested size."""
        chunk, self._stderr_remainder = await self._read_bounded(
            self._stderr,
            self._stderr_remainder,
            size,
        )
        return chunk

    @staticmethod
    async def _read_bounded(
        queue: asyncio.Queue[bytes],
        remainder: bytes,
        size: int,
    ) -> tuple[bytes, bytes]:
        """Return at most ``size`` bytes without discarding a frame suffix."""
        if size <= 0:
            return b"", remainder
        payload = remainder or await queue.get()
        return payload[:size], payload[size:]

    async def write_stdin(self, data: bytes) -> None:
        """Write protocol bytes to the attached exec stdin."""
        await asyncio.get_running_loop().run_in_executor(self._executor, self._socket.sendall, data)

    async def close_stdin(self) -> None:
        """Half-close the attached socket's write direction."""
        with suppress(OSError):
            self._socket.shutdown(socket.SHUT_WR)

    async def wait(self) -> tuple[int | None, int | None]:
        """Wait for exec completion and return its clean exit code when available."""
        with suppress(Exception):
            await self._reader_task
        try:
            info: dict[str, Any] = await asyncio.get_running_loop().run_in_executor(
                self._executor,
                self._client.api.exec_inspect,
                self._exec_id,
            )
        except Exception:
            return None, None
        exit_code = info.get("ExitCode")
        return (int(exit_code) if exit_code is not None else None, None)

    async def terminate(self) -> None:
        """Kill the disposable container that owns the attached exec."""
        container = await asyncio.get_running_loop().run_in_executor(
            self._executor,
            self._client.containers.get,
            self._container_id,
        )
        with suppress(Exception):
            await asyncio.get_running_loop().run_in_executor(
                self._executor,
                lambda: container.kill(signal="SIGKILL"),
            )
