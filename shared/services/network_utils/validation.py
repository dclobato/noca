#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Validation helpers for safe outbound HTTP requests."""

from __future__ import annotations

import ipaddress
import socket
from typing import Any
from urllib.parse import ParseResult, urlparse

from fastapi import Request

from .errors import SSRFProtectionError

BLOCKED_NETWORKS: set[str] = {
    "127.0.0.0/8",
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "169.254.0.0/16",
    "0.0.0.0/8",
    "100.64.0.0/10",
    "192.0.0.0/24",
    "192.0.2.0/24",
    "198.18.0.0/15",
    "198.51.100.0/24",
    "203.0.113.0/24",
    "224.0.0.0/4",
    "240.0.0.0/4",
    "255.255.255.255/32",
    "::1/128",
    "::/128",
    "::ffff:0:0/96",
    "::ffff:0:0:0/96",
    "64:ff9b::/96",
    "64:ff9b:1::/48",
    "100::/64",
    "2001::/32",
    "2001:10::/28",
    "2001:20::/28",
    "2001:db8::/32",
    "2002::/16",
    "fc00::/7",
    "fe80::/10",
    "ff00::/8",
}


def check_ip_against_blocked_networks(
    ip_obj: ipaddress.IPv4Address | ipaddress.IPv6Address,
    ip_str: str,
    hostname: str,
) -> None:
    """Check whether an IP address belongs to any blocked network."""
    for blocked_cidr in BLOCKED_NETWORKS:
        try:
            network = ipaddress.ip_network(blocked_cidr)
            if ip_obj in network:
                ip_version = "IPv6" if ip_obj.version == 6 else "IPv4"
                raise SSRFProtectionError(
                    f"Access denied: '{hostname}' resolves to {ip_version} address in a private/localhost network. "
                    f"IP {ip_str} belongs to {blocked_cidr}. "
                    f"Requests to internal networks are not allowed (SSRF protection)."
                )
        except ValueError:
            continue


def validate_not_private_network(hostname: str) -> None:
    """Validate that a hostname does not resolve to a private network or localhost."""
    try:
        ip_obj = ipaddress.ip_address(hostname)
        check_ip_against_blocked_networks(ip_obj, hostname, hostname)
        return
    except ValueError:
        pass

    try:
        addr_info = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        for _, _, _, _, sockaddr in addr_info:
            ip_str = str(sockaddr[0])
            try:
                ip_obj = ipaddress.ip_address(ip_str)
                check_ip_against_blocked_networks(ip_obj, ip_str, hostname)
            except ValueError:
                continue
    except socket.gaierror, OSError:
        pass


def get_ip_from_request(request: Request) -> str | None:
    """Safely extract the client IP address from a request."""
    max_ip_length = 45
    max_forwarded_for_length = 200
    forwarded_for = request.headers.get("X-Forwarded-For", "").strip()

    if forwarded_for and len(forwarded_for) <= max_forwarded_for_length:
        candidate_ip = forwarded_for.split(",")[0].strip()
        if candidate_ip and len(candidate_ip) <= max_ip_length:
            try:
                return str(ipaddress.ip_address(candidate_ip))
            except ValueError:
                pass

    if request.client and request.client.host:
        try:
            if len(request.client.host) <= max_ip_length:
                return str(ipaddress.ip_address(request.client.host))
        except ValueError:
            pass
    return None


def validate_and_parse_url(
    url: str | None = None,
    block_private_networks: bool = False,
    allowed_schemes: set[str] | None = None,
) -> ParseResult:
    """Validate and parse a URL, including optional SSRF protection."""
    schemes = allowed_schemes or {"http", "https"}
    if not url or not isinstance(url, str):
        raise ValueError("URL must be a non-empty string")

    url = url.strip()
    try:
        parsed = urlparse(url)
    except Exception as exc:
        raise ValueError(f"Malformed URL: {exc}") from exc

    if not parsed.scheme:
        raise ValueError("URL must include a scheme (e.g. http:// or https://)")
    if parsed.scheme.lower() not in schemes:
        raise ValueError(f"Scheme '{parsed.scheme}' is not allowed. Accepted schemes: {', '.join(sorted(schemes))}")
    if not parsed.hostname:
        raise ValueError("URL must contain a valid hostname")
    if block_private_networks:
        validate_not_private_network(parsed.hostname)
    return parsed


def sanitize_params(
    params: dict[str, Any] | None,
    max_params: int = 100,
    max_depth: int = 5,
    _current_depth: int = 0,
) -> dict[str, Any] | None:
    """Sanitize query string parameters for safe use in HTTP requests."""
    if params is None or not params:
        return None
    if not isinstance(params, dict):
        raise ValueError(f"Parameters must be a dictionary, got: {type(params).__name__}")
    if _current_depth > max_depth:
        raise ValueError(f"Maximum nesting depth exceeded ({max_depth}).")
    if _current_depth == 0 and count_total_params(params) > max_params:
        raise ValueError(
            f"Total parameter count exceeded ({count_total_params(params)}). Maximum allowed: {max_params}"
        )

    sanitized: dict[str, Any] = {}
    for key, value in params.items():
        clean_key = str(key).strip()
        if not clean_key:
            raise ValueError("Parameter with empty key detected")
        sanitized[clean_key] = sanitize_value(value, max_depth, _current_depth + 1)
    return sanitized


def sanitize_value(value: Any, max_depth: int, current_depth: int) -> Any:
    """Sanitize an individual value, recursively processing complex structures."""
    if value is None:
        return ""
    if isinstance(value, dict):
        if current_depth > max_depth:
            raise ValueError(f"Maximum nesting depth exceeded ({max_depth})")
        sanitized_dict: dict[str, Any] = {}
        for key, nested_value in value.items():
            clean_key = str(key).strip()
            if not clean_key:
                raise ValueError("Empty key detected in nested dictionary")
            sanitized_dict[clean_key] = sanitize_value(nested_value, max_depth, current_depth + 1)
        return sanitized_dict
    if isinstance(value, (list, tuple)):
        if current_depth > max_depth:
            raise ValueError(f"Maximum nesting depth exceeded ({max_depth})")
        return [sanitize_value(item, max_depth, current_depth + 1) for item in value]
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def count_total_params(obj: Any) -> int:
    """Recursively count the total number of parameters in a structure."""
    if isinstance(obj, dict):
        return len(obj) + sum(count_total_params(value) for value in obj.values())
    if isinstance(obj, (list, tuple)):
        return len(obj) + sum(count_total_params(item) for item in obj)
    return 0


def is_private_network(hostname: str) -> bool:
    """Check if a hostname resolves to a private network or localhost."""
    try:
        validate_not_private_network(hostname)
        return False
    except SSRFProtectionError:
        return True


def sanitize_headers(headers: dict[str, Any] | None) -> dict[str, str] | None:
    """Sanitize HTTP headers for safe use in requests."""
    if headers is None or not headers:
        return None
    if not isinstance(headers, dict):
        raise ValueError(f"Headers must be a dictionary, got: {type(headers).__name__}")

    sanitized: dict[str, str] = {}
    for key, value in headers.items():
        key_str = str(key).strip()
        value_str = str(value)
        if "\r" in value_str or "\n" in value_str:
            raise ValueError(
                f"Header '{key_str}' contains forbidden control characters (CRLF). "
                "This may indicate an HTTP header injection attempt."
            )
        if "\x00" in value_str:
            raise ValueError(f"Header '{key_str}' contains a null byte (\\x00), which is not allowed")
        if not key_str or not key_str.isprintable():
            raise ValueError(f"Invalid header name: '{key_str}'. Header names must contain only printable characters.")
        sanitized[key_str] = value_str
    return sanitized


def build_safe_request_kwargs(
    url: str,
    params: dict[str, Any] | None = None,
    headers: dict[str, Any] | None = None,
    timeout: int = 30,
    block_private_networks: bool = False,
    allowed_schemes: set[str] | None = None,
    max_params: int = 100,
    max_redirects: int = 5,
    default_headers: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any], int]:
    """Build safe kwargs for an HTTP request after full validation."""
    parsed_url = validate_and_parse_url(
        url,
        block_private_networks=block_private_networks,
        allowed_schemes=allowed_schemes,
    )
    safe_params = sanitize_params(params, max_params=max_params)
    safe_custom_headers = sanitize_headers(headers)

    final_headers: dict[str, Any] = {}
    if default_headers:
        final_headers.update(default_headers)
    if safe_custom_headers:
        final_headers.update(safe_custom_headers)

    kwargs: dict[str, Any] = {"timeout": timeout, "allow_redirects": True, "stream": True}
    if safe_params:
        kwargs["params"] = safe_params
    if final_headers:
        kwargs["headers"] = final_headers
    return parsed_url.geturl(), kwargs, max_redirects
