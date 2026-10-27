#!/usr/bin/env python3
"""Validate an inference URL before a diagnostic or smoke test contacts it."""

from __future__ import annotations

import ipaddress
import sys
from urllib.parse import urlparse


def _is_local_address(hostname: str) -> bool:
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return False
    return (
        not address.is_unspecified
        and not address.is_multicast
        and (address.is_loopback or address.is_private or address.is_link_local)
    )


def validate(value: str, *, allow_external: bool = False, loopback_only: bool = True) -> str:
    target = value.strip().rstrip("/")
    parsed = urlparse(target)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("inference URL must be an HTTP(S) URL with a hostname")
    if parsed.username or parsed.password:
        raise ValueError("inference URL must not embed credentials")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("inference URL contains an invalid port") from exc
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("inference URL contains an invalid port")

    hostname = parsed.hostname.rstrip(".").lower()
    local = _is_local_address(hostname)
    if not local:
        local = (
            hostname in {"localhost", "localhost6"}
            or hostname.endswith((".local", ".lan", ".internal"))
            or "." not in hostname
        )
    loopback = hostname in {"localhost", "localhost6"} or (
        _is_local_address(hostname)
        and ipaddress.ip_address(hostname).is_loopback
    )
    if loopback_only and not loopback:
        raise ValueError("inference URL must point to loopback in MOSS_LOCAL_ONLY mode")
    if not local and not allow_external:
        raise ValueError(
            f"inference URL points outside the local network ({hostname}); "
            "set MOSS_ALLOW_EXTERNAL_INFERENCE=true only for an intentional "
            "external integration"
        )
    return target


def main(argv: list[str]) -> int:
    if len(argv) not in {2, 3, 4}:
        print(
            "usage: validate_inference_url.py URL [allow_external=true|false] [loopback_only=true|false]",
            file=sys.stderr,
        )
        return 2
    allow_external = len(argv) >= 3 and argv[2].strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    loopback_only = not (len(argv) == 4 and argv[3].strip().lower() in {"0", "false", "no", "off"})
    try:
        target = validate(argv[1], allow_external=allow_external, loopback_only=loopback_only)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"inference_target=allowed ({target})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
