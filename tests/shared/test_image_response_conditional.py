#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The shared image response carries a validator and can answer ``304``.

Before this, ``build_image_response`` set only ``Cache-Control``. A route that
asked the client to revalidate therefore got a full re-download on every
revalidation, because there was nothing for the client to revalidate *against*.
On a list page emitting one avatar per row that was one full fetch per row per
view (#199). These tests pin the contract the fix relies on.
"""

from __future__ import annotations

import hashlib
import logging

import pytest
from fastapi import Request

from shared.services.imageprocessing_service import ImageProcessingService

PNG = b"\x89PNG\r\n\x1a\n" + b"not really a png but bytes are bytes" * 8


def _service() -> ImageProcessingService:
    return ImageProcessingService(logger=logging.getLogger(__name__))


def _request(**headers: str) -> Request:
    raw = [(name.lower().encode("latin-1"), value.encode("latin-1")) for name, value in headers.items()]
    return Request({"type": "http", "method": "GET", "path": "/", "headers": raw, "query_string": b""})


def _expected_etag(data: bytes) -> str:
    return f'"{hashlib.sha256(data).hexdigest()}"'


def test_every_response_carries_a_strong_content_etag() -> None:
    """The tag is derived from the bytes, quoted, and present even with no request."""
    response = _service().build_image_response(PNG, "image/png")

    assert response.status_code == 200
    assert response.headers["etag"] == _expected_etag(PNG)
    assert response.headers["cache-control"].startswith("public, max-age=")


def test_the_same_bytes_get_the_same_tag_whatever_the_directive() -> None:
    """The ETag identifies the representation; the directive is policy, not identity."""
    service = _service()
    public = service.build_image_response(PNG, cache_directive="public")
    private = service.build_image_response(PNG, cache_directive="private")

    assert public.headers["etag"] == private.headers["etag"]
    assert public.headers["cache-control"] != private.headers["cache-control"]


def test_different_bytes_get_different_tags() -> None:
    service = _service()
    assert service.build_image_response(PNG).headers["etag"] != service.build_image_response(PNG + b"x").headers["etag"]


def test_a_matching_if_none_match_answers_304_with_no_body() -> None:
    """The whole point: a revalidation costs a header exchange, not the image."""
    response = _service().build_image_response(
        PNG, "image/png", request=_request(**{"If-None-Match": _expected_etag(PNG)})
    )

    assert response.status_code == 304
    assert response.body == b""
    # A 304 must repeat the validator and the caching policy, and nothing else
    # that describes a body it does not carry.
    assert response.headers["etag"] == _expected_etag(PNG)
    assert response.headers["cache-control"].startswith("public, max-age=")
    assert "content-type" not in response.headers


def test_a_304_keeps_the_callers_cache_directive() -> None:
    """A private image stays private when it is not resent."""
    response = _service().build_image_response(
        PNG, cache_directive="private", request=_request(**{"If-None-Match": _expected_etag(PNG)})
    )

    assert response.status_code == 304
    assert response.headers["cache-control"].startswith("private, max-age=")


@pytest.mark.parametrize(
    "header",
    [
        'W/"{etag}"',  # weak comparison: the client may send a weak form of a strong tag
        '"other", {quoted}',  # a list, with the match not first
    ],
)
def test_weak_and_listed_tags_still_match(header: str) -> None:
    etag = _expected_etag(PNG)
    value = header.format(etag=etag.strip('"'), quoted=etag)

    response = _service().build_image_response(PNG, request=_request(**{"If-None-Match": value}))

    assert response.status_code == 304


def test_a_stale_tag_gets_the_image() -> None:
    """A client holding an older version must be sent the new bytes."""
    response = _service().build_image_response(PNG, request=_request(**{"If-None-Match": '"stale"'}))

    assert response.status_code == 200
    assert response.body == PNG


def test_a_request_without_a_validator_gets_the_image() -> None:
    response = _service().build_image_response(PNG, request=_request())

    assert response.status_code == 200
    assert response.headers["etag"] == _expected_etag(PNG)
