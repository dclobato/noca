#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Batch-import payload parsing helpers for contest users."""

from __future__ import annotations

import csv
import io
import json
from typing import cast

from .models import _CSV_OPTIONAL_HEADERS, _CSV_REQUIRED_HEADERS, BatchUserRow


def parse_batch_upload(slug: str, filename: str, content: bytes) -> list[BatchUserRow]:
    """Parse an uploaded batch user file into raw row dictionaries."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ("csv", "json"):
        raise ValueError("Only .csv and .json files are accepted.")

    if len(content) > 5 * 1024 * 1024:
        raise ValueError("File exceeds the 5 MB size limit.")

    try:
        if ext == "json":
            return normalize_batch_users_payload(slug, json.loads(content.decode("utf-8")))

        reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
        if reader.fieldnames is None:
            raise ValueError("Invalid File Format: CSV header row is missing.")
        normalized_headers = {header.strip() for header in reader.fieldnames if header}
        has_required_headers = _CSV_REQUIRED_HEADERS.issubset(normalized_headers)
        has_only_allowed_headers = normalized_headers.issubset(_CSV_REQUIRED_HEADERS | _CSV_OPTIONAL_HEADERS)
        if not has_required_headers or not has_only_allowed_headers:
            raise ValueError(
                "Invalid File Format: CSV headers must include username, fullname, role, password and may add"
                " email, site, location."
            )
        return cast(list[BatchUserRow], list(reader))
    except ValueError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Invalid File Format: {exc}") from exc


def normalize_batch_users_payload(slug: str, raw_payload: object) -> list[BatchUserRow]:
    """Normalize a parsed JSON batch payload into user row dictionaries."""
    if isinstance(raw_payload, list):
        return cast(list[BatchUserRow], raw_payload)
    if not isinstance(raw_payload, dict):
        raise ValueError("Invalid File Format: JSON must be an object or list.")

    payload_slug = raw_payload.get("contest-slug")
    if payload_slug and payload_slug != slug:
        raise ValueError("Invalid File Format: contest-slug does not match this contest.")

    users = raw_payload.get("users")
    if not isinstance(users, list):
        raise ValueError("Invalid File Format: JSON object must contain a users list.")
    return cast(list[BatchUserRow], users)
