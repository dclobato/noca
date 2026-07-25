#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Fail-fast multipart file-size enforcement for NOCA ASGI applications."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from re import Pattern
from typing import TYPE_CHECKING

from fastapi import HTTPException
from python_multipart import MultipartParser
from python_multipart.exceptions import MultipartParseError
from python_multipart.multipart import parse_options_header
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

if TYPE_CHECKING:
    from python_multipart.multipart import MultipartCallbacks


class MultipartFileTooLargeError(HTTPException):
    """Represent a streamed multipart file that exceeded its configured ceiling."""

    def __init__(self, label: str, max_file_size: int) -> None:
        """Build an HTTP 413 error with a user-facing size message."""
        maximum_megabytes = max_file_size / (1024 * 1024)
        super().__init__(
            status_code=413,
            detail=(f"{label} file too large. Maximum allowed: {maximum_megabytes:.1f} MB."),
        )


@dataclass(frozen=True)
class MultipartFileSizeRule:
    """Match one upload route and define which file fields share its ceiling."""

    path_pattern: str
    max_file_size: int
    label: str
    field_names: frozenset[str] | None = None
    method: str = "POST"
    _compiled_path: Pattern[str] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Validate and compile the route pattern."""
        if self.max_file_size <= 0:
            raise ValueError("max_file_size must be positive")
        if not self.label.strip():
            raise ValueError("label must not be empty")
        object.__setattr__(self, "_compiled_path", re.compile(self.path_pattern))

    def matches(self, scope: Scope) -> bool:
        """Return whether this rule applies to the ASGI request scope."""
        method_matches = scope.get("method") == self.method
        path_matches = bool(self._compiled_path.fullmatch(scope.get("path", "")))
        return method_matches and path_matches


class _MultipartFileSizeTracker:
    """Track selected file bytes while python-multipart scans a request stream."""

    def __init__(self, rule: MultipartFileSizeRule) -> None:
        """Initialize multipart callbacks for one request."""
        self.rule = rule
        self.file_bytes_seen = 0
        self.current_is_limited_file = False
        self.current_header_name = bytearray()
        self.current_header_value = bytearray()
        self.content_disposition: bytes | None = None

    def callbacks(self) -> MultipartCallbacks:
        """Return callbacks consumed by ``python_multipart.MultipartParser``."""
        return {
            "on_part_begin": self.on_part_begin,
            "on_part_data": self.on_part_data,
            "on_header_field": self.on_header_field,
            "on_header_value": self.on_header_value,
            "on_header_end": self.on_header_end,
            "on_headers_finished": self.on_headers_finished,
        }

    def on_part_begin(self) -> None:
        """Reset state for a new multipart part."""
        self.current_is_limited_file = False
        self.content_disposition = None

    def on_part_data(self, data: bytes, start: int, end: int) -> None:
        """Reject the stream as soon as selected file bytes exceed the ceiling."""
        if not self.current_is_limited_file:
            return
        self.file_bytes_seen += end - start
        if self.file_bytes_seen > self.rule.max_file_size:
            raise MultipartFileTooLargeError(self.rule.label, self.rule.max_file_size)

    def on_header_field(self, data: bytes, start: int, end: int) -> None:
        """Collect a multipart header name, which may arrive in chunks."""
        self.current_header_name.extend(data[start:end])

    def on_header_value(self, data: bytes, start: int, end: int) -> None:
        """Collect a multipart header value, which may arrive in chunks."""
        self.current_header_value.extend(data[start:end])

    def on_header_end(self) -> None:
        """Capture Content-Disposition and clear the header buffers."""
        if self.current_header_name.lower() == b"content-disposition":
            self.content_disposition = bytes(self.current_header_value)
        self.current_header_name.clear()
        self.current_header_value.clear()

    def on_headers_finished(self) -> None:
        """Mark whether the current multipart part is a selected file field."""
        _, options = parse_options_header(self.content_disposition)
        field_name = options.get(b"name", b"").decode("latin-1")
        is_file = b"filename" in options
        limited_field_names = self.rule.field_names
        field_is_selected = limited_field_names is None or field_name in limited_field_names
        self.current_is_limited_file = is_file and field_is_selected


class MultipartFileSizeLimitMiddleware:
    """Stop oversized selected files while their multipart body streams."""

    def __init__(self, app: ASGIApp, *, rules: tuple[MultipartFileSizeRule, ...]) -> None:
        """Store the wrapped application and ordered route rules."""
        if not rules:
            raise ValueError("rules must not be empty")
        self.app = app
        self.rules = rules

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Enforce a matching rule without buffering the complete request body."""
        parser = self._build_parser(scope)
        if parser is None:
            await self.app(scope, receive, send)
            return

        async def limited_receive() -> Message:
            nonlocal parser
            message = await receive()
            if parser is None or message["type"] != "http.request":
                return message

            try:
                parser.write(message.get("body", b""))
                if not message.get("more_body", False):
                    parser.finalize()
            except MultipartParseError:
                parser = None
            return message

        await self.app(scope, limited_receive, send)

    def _build_parser(self, scope: Scope) -> MultipartParser | None:
        """Build a tracker when a multipart request matches a configured rule."""
        if scope["type"] != "http":
            return None
        rule = next((candidate for candidate in self.rules if candidate.matches(scope)), None)
        if rule is None:
            return None

        content_type, options = parse_options_header(Headers(scope=scope).get("content-type"))
        boundary = options.get(b"boundary")
        if content_type != b"multipart/form-data" or boundary is None:
            return None

        tracker = _MultipartFileSizeTracker(rule)
        return MultipartParser(boundary, tracker.callbacks())
