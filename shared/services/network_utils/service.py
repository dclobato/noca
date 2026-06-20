#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Safe outbound HTTP request service."""

from __future__ import annotations

import json
import logging
from typing import Any

import requests
from requests.exceptions import ConnectionError, HTTPError, RequestException, Timeout

from .errors import NetworkServiceError
from .validation import (
    build_safe_request_kwargs,
    get_ip_from_request,
    is_private_network,
    sanitize_headers,
    sanitize_params,
    validate_and_parse_url,
    validate_not_private_network,
)


class NetworkService:
    """Service for making safe HTTP requests with validation and SSRF protection."""

    DEFAULT_TIMEOUT = 10
    MAX_RESPONSE_SIZE = 10 * 1024 * 1024

    def __init__(self, logger: logging.Logger | None = None) -> None:
        """Initialize the NetworkService."""
        self._logger = logger or logging.getLogger(__name__)

    _validate_not_private_network = staticmethod(validate_not_private_network)
    get_ip_from_request = staticmethod(get_ip_from_request)
    validate_and_parse_url = staticmethod(validate_and_parse_url)
    sanitize_params = staticmethod(sanitize_params)
    is_private_network = staticmethod(is_private_network)
    sanitize_headers = staticmethod(sanitize_headers)
    build_safe_request_kwargs = staticmethod(build_safe_request_kwargs)

    def make_json_request(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        header: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Perform a safe HTTP GET request that returns JSON."""
        default_headers: dict[str, Any] = {
            "Accept": "application/json",
            "Accept-Encoding": "gzip, deflate",
        }
        validated_url, kwargs, max_redirects = build_safe_request_kwargs(
            url=url,
            params=params,
            headers=header,
            timeout=self.DEFAULT_TIMEOUT,
            default_headers=default_headers,
        )

        try:
            session = requests.Session()
            session.max_redirects = max_redirects
            response = session.get(validated_url, **kwargs)
            response.raise_for_status()

            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > self.MAX_RESPONSE_SIZE:
                response.close()
                raise NetworkServiceError(
                    f"Response too long ({content_length} bytes, max allowed is {self.MAX_RESPONSE_SIZE})"
                )

            content = b""
            for chunk in response.iter_content(chunk_size=8192):
                content += chunk
                if len(content) > self.MAX_RESPONSE_SIZE:
                    response.close()
                    raise NetworkServiceError(f"Response exceeded max size of {self.MAX_RESPONSE_SIZE} bytes")

            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError:
                text = self._decode_with_fallback(content)

            try:
                data = json.loads(text)
            except json.JSONDecodeError as exc:
                snippet = text[:200] + ("..." if len(text) > 200 else "")
                raise NetworkServiceError(f"Error while processing JSON: {exc}. Start of response: {snippet}") from exc
            if not isinstance(data, dict):
                raise NetworkServiceError(f"JSON response is not an object/dict. Actual type: {type(data).__name__}")
            return data
        except Timeout as exc:
            raise NetworkServiceError(
                f"Timeout: request to {validated_url} exceeded {self.DEFAULT_TIMEOUT} seconds"
            ) from exc
        except ConnectionError as exc:
            raise NetworkServiceError(
                f"Connection error to {validated_url}: check network connectivity and whether the server is reachable"
            ) from exc
        except HTTPError as exc:
            raise self._map_http_error(validated_url, exc) from exc
        except RequestException as exc:
            raise NetworkServiceError(f"Error while requesting {validated_url}: {exc}") from exc
        except ValueError, NetworkServiceError:
            raise
        except Exception as exc:
            raise NetworkServiceError(
                f"Unexpected error while requesting {validated_url}: {type(exc).__name__}: {exc}"
            ) from exc

    def _decode_with_fallback(self, content: bytes) -> str:
        """Decode response bytes with common encoding fallbacks."""
        for encoding in ["latin-1", "iso-8859-1", "cp1252"]:
            try:
                return content.decode(encoding)
            except UnicodeDecodeError:
                continue
        raise NetworkServiceError("Could not decode response body")

    def _map_http_error(self, validated_url: str, exc: HTTPError) -> NetworkServiceError:
        """Translate HTTP errors to service errors with user-facing detail."""
        response = exc.response
        if response is None:
            return NetworkServiceError(f"HTTP error while requesting {validated_url}: {exc}")

        status_code = response.status_code
        if 500 <= status_code < 600:
            return NetworkServiceError(
                f"Server error {status_code} while requesting {validated_url}: {response.reason}"
            )
        if 400 <= status_code < 500:
            error_detail = ""
            try:
                error_body = response.json()
                error_detail = f" - {error_body.get('error', error_body.get('message', ''))}"
            except Exception:
                pass
            return NetworkServiceError(f"HTTP error {status_code}: {response.reason}{error_detail}")
        return NetworkServiceError(f"Unexpected HTTP error {status_code}: {response.reason}")
