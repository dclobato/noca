#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Compatibility re-exports for shared network validation helpers."""

from shared.services.network_utils.validation import (
    BLOCKED_NETWORKS,
    build_safe_request_kwargs,
    check_ip_against_blocked_networks,
    count_total_params,
    get_ip_from_request,
    is_private_network,
    sanitize_headers,
    sanitize_params,
    sanitize_value,
    validate_and_parse_url,
    validate_not_private_network,
)

__all__ = [
    "BLOCKED_NETWORKS",
    "build_safe_request_kwargs",
    "check_ip_against_blocked_networks",
    "count_total_params",
    "get_ip_from_request",
    "is_private_network",
    "sanitize_headers",
    "sanitize_params",
    "sanitize_value",
    "validate_and_parse_url",
    "validate_not_private_network",
]
