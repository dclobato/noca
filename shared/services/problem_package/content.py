#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Content-level validation of individual package payloads."""

from __future__ import annotations

import codecs
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import DependencyError, EmptyFileError, PdfReadError

from shared.problem_statement_markdown import validate_md_content
from shared.services.problem_image import EXT_TO_MIME
from shared.services.problem_package.constants import (
    MAX_EDITORIAL_BYTES,
    MAX_EXPLANATION_BYTES,
    MAX_MARKDOWN_BYTES,
    MAX_PDF_BYTES,
    PDF_MAGIC,
)
from shared.services.problem_package.errors import PackageError
from shared.services.problem_package.model import PackageStatement

_NORMALIZE_CHUNK_BYTES = 1024 * 1024


def read_markdown_statement(path: Path) -> PackageStatement:
    """Validate ``statement.md`` and return the statement record.

    Raises:
        PackageError: If the file is oversized, not UTF-8, or invalid Markdown.
    """
    size = path.stat().st_size
    if size > MAX_MARKDOWN_BYTES:
        raise PackageError(f"statement.md is {size} bytes; the limit is {MAX_MARKDOWN_BYTES}.")
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise PackageError(f"Invalid statement.md: not valid UTF-8 ({exc}).") from exc
    errors = validate_md_content(text)
    if errors:
        raise PackageError(f"Invalid statement.md: {'; '.join(errors)}")
    return PackageStatement(kind="md", path=path, text=text)


def read_markdown_editorial(path: Path) -> str:
    """Validate and decode ``editorial.md`` with the statement Markdown policy."""
    size = path.stat().st_size
    if size > MAX_EDITORIAL_BYTES:
        raise PackageError(f"editorial.md is {size} bytes; the limit is {MAX_EDITORIAL_BYTES}.")
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise PackageError(f"Invalid editorial.md: not valid UTF-8 ({exc}).") from exc
    errors = validate_md_content(text)
    if errors:
        raise PackageError(f"Invalid editorial.md: {'; '.join(errors)}")
    return text


def read_pdf_statement(path: Path) -> PackageStatement:
    """Validate ``statement.pdf`` and return the statement record.

    The reader is deliberately non-strict: a PDF produced by LaTeX or Word may
    violate minor points of the specification while rendering fine in every
    viewer, and refusing those would reject legitimate statements. What is not
    tolerated is a file that is not a PDF, is encrypted, or has no readable page.

    Raises:
        PackageError: If the file fails any of those checks.
    """
    size = path.stat().st_size
    if size > MAX_PDF_BYTES:
        raise PackageError(f"statement.pdf is {size} bytes; the limit is {MAX_PDF_BYTES}.")
    with path.open("rb") as handle:
        if handle.read(len(PDF_MAGIC)) != PDF_MAGIC:
            raise PackageError("Invalid statement.pdf: the file does not start with a PDF signature.")
    try:
        reader = PdfReader(str(path), strict=False)
        if reader.is_encrypted:
            raise PackageError("Invalid statement.pdf: encrypted PDF statements are not supported.")
        if len(reader.pages) < 1:
            raise PackageError("Invalid statement.pdf: the document has no pages.")
        # Resolve the first page so a structurally broken page tree fails here,
        # where it can still be reported as a package error.
        first_page = reader.pages[0]
        if first_page is None:
            raise PackageError("Invalid statement.pdf: the first page could not be read.")
    except (PdfReadError, DependencyError, EmptyFileError) as exc:
        raise PackageError(f"Invalid statement.pdf: {exc}") from exc
    return PackageStatement(kind="pdf", path=path, text=None)


def read_explanation(path: Path, ordinal: int) -> str | None:
    """Decode one ``explanation/NNN.txt`` member.

    Raises:
        PackageError: If the file is oversized or not valid UTF-8.
    """
    size = path.stat().st_size
    if size > MAX_EXPLANATION_BYTES:
        raise PackageError(
            f"Explanation for test case {ordinal} is {size} bytes; the limit is {MAX_EXPLANATION_BYTES}."
        )
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise PackageError(f"Explanation for test case {ordinal} is not valid UTF-8.") from exc
    return text.strip() or None


def decode_test_case(path: Path, *, ordinal: int, stream: str) -> None:
    """Assert one staged test-case side decodes as UTF-8.

    Binary test cases are unsupported in both domains; the previous Contest path
    accepted them only because nothing on that side ever checked. The check is
    incremental so a large case is never held in memory: an incremental decoder
    carries a partial multi-byte sequence across the chunk boundary.

    Raises:
        PackageError: When the bytes are not valid UTF-8.
    """
    decoder = codecs.getincrementaldecoder("utf-8")()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(_NORMALIZE_CHUNK_BYTES):
                decoder.decode(chunk)
        decoder.decode(b"", final=True)
    except UnicodeDecodeError as exc:
        raise PackageError(
            f"Test case {ordinal:03d} {stream} is not valid UTF-8 text; binary test cases are not supported."
        ) from exc


def normalize_in_place(path: Path) -> int:
    """Rewrite a staged file with LF line endings and return its new size.

    Normalization happens **after** hashing, so an integrity manifest describes
    the bytes the package actually shipped rather than a derived form.

    The rewrite streams through a sibling file rather than loading the member:
    a test case may legitimately be tens of megabytes, and reading one whole is
    the kind of thing the disk-backed design exists to avoid. A ``\r`` that lands
    on a chunk boundary is carried into the next chunk so a split ``\r\n`` is
    still collapsed to a single ``\n``.
    """
    temporary = path.with_name(f".{path.name}.normalizing")
    written = 0
    pending_cr = False
    try:
        with path.open("rb") as source, temporary.open("wb") as sink:
            while chunk := source.read(_NORMALIZE_CHUNK_BYTES):
                if pending_cr:
                    # The previous chunk ended in CR: it is a line ending either
                    # way, and a following LF belongs to the same one.
                    chunk = chunk[1:] if chunk.startswith(b"\n") else chunk
                    sink.write(b"\n")
                    written += 1
                pending_cr = chunk.endswith(b"\r")
                if pending_cr:
                    chunk = chunk[:-1]
                converted = chunk.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
                sink.write(converted)
                written += len(converted)
            if pending_cr:
                sink.write(b"\n")
                written += 1
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return written


def image_mime_for(member: str) -> str:
    """Return the MIME type implied by a packaged image member's extension."""
    extension = member.rsplit(".", 1)[-1].lower() if "." in member else ""
    mime = EXT_TO_MIME.get(extension)
    if mime is None:
        raise PackageError(f"Packaged image {member!r} has an unsupported extension.")
    return mime
