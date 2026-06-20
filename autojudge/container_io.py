#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""
Docker container file I/O helpers (synchronous — run in ThreadPoolExecutor).

All functions operate on a live Container object. File injection uses an
in-memory tar stream so no temp files are created on the host. File extraction
also uses tar streams via get_archive().
"""

import io
import shlex
import tarfile
from pathlib import Path

import docker
import docker.errors
from docker.models.containers import Container


def _put_bytes(container: Container, data: bytes, container_path: str) -> None:
    """
    Inject bytes into a container as a file at container_path.

    Uses an in-memory tar stream — no temp files on the host.
    The file is created with mode 0o755 (executable, for binaries).

    Args:
        container: Live Docker container.
        data: Raw bytes to inject.
        container_path: Absolute path inside the container.
    """
    filename = Path(container_path).name
    dest_dir = str(Path(container_path).parent)

    buf = io.BytesIO()
    info = tarfile.TarInfo(name=filename)
    info.size = len(data)
    info.mode = 0o755

    with tarfile.open(fileobj=buf, mode="w") as tar:
        tar.addfile(info, io.BytesIO(data))

    buf.seek(0)
    container.put_archive(path=dest_dir, data=buf)


def _get_file_bytes(container: Container, container_path: str) -> bytes:
    """
    Extract a file from a container and return its raw bytes.

    Args:
        container: Live Docker container.
        container_path: Absolute path inside the container.

    Raises:
        FileNotFoundError: If the path does not exist inside the container.
    """
    try:
        stream, _ = container.get_archive(container_path)
        buf = io.BytesIO()
        for chunk in stream:
            buf.write(chunk)
        buf.seek(0)
        with tarfile.open(fileobj=buf) as tar:
            member = tar.getmembers()[0]
            f = tar.extractfile(member)
            if f is None:
                raise FileNotFoundError(f"{container_path} is not a regular file")
            return f.read()
    except docker.errors.NotFound as exc:
        raise FileNotFoundError(f"{container_path} not found in container") from exc


def _get_file_bytes_safe(container: Container, container_path: str, max_bytes: int) -> bytes:
    """
    Like _get_file_bytes but returns b"" on any error and caps at max_bytes.

    Args:
        container: Live Docker container.
        container_path: Absolute path inside the container.
        max_bytes: Maximum bytes to return.
    """
    try:
        data = _get_file_bytes(container, container_path)
        return data[:max_bytes]
    except Exception:
        return b""


def _get_file_text_safe(container: Container, container_path: str, max_bytes: int) -> str | None:
    """
    Like _get_file_bytes_safe but decodes to text.

    Args:
        container: Live Docker container.
        container_path: Absolute path inside the container.
        max_bytes: Maximum bytes to read before decoding.

    Returns:
        Decoded text, or None if the file cannot be read.
    """
    try:
        return _get_file_bytes(container, container_path)[:max_bytes].decode(errors="replace")
    except Exception:
        return None


def _get_file_size_safe(container: Container, container_path: str) -> int | None:
    """
    Return the byte size of a file inside a container using stat.

    Args:
        container: Live Docker container.
        container_path: Absolute path inside the container.

    Returns:
        File size in bytes, or None if the file does not exist or stat fails.
    """
    quoted_path = shlex.quote(container_path)
    result = container.exec_run(
        ["sh", "-c", f"if [ -f {quoted_path} ]; then stat -c %s {quoted_path}; fi"],
        user="root",
        demux=False,
    )
    if result.exit_code != 0:
        return None
    output = (result.output or b"").decode(errors="replace").strip()
    if not output:
        return None
    try:
        return int(output)
    except ValueError:
        return None
