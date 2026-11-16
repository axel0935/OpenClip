"""SSRF-safe validation for remote media URLs."""

import ipaddress
import socket
from collections.abc import Callable
from urllib.parse import urlparse

from openclip.ingest.errors import IngestFailure

Resolver = Callable[[str, int], list[str]]


def _default_resolver(host: str, port: int) -> list[str]:
    """Resolve a host to every IP address it maps to."""
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


def _is_public_address(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Return True only for globally routable unicast addresses."""
    return ip.is_global and not (
        ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_multicast or ip.is_reserved
    )


def validate_download_url(url: str, resolver: Resolver | None = None) -> str:
    """Validate an HTTP(S) download URL against SSRF rules.

    The URL must use the ``http`` or ``https`` scheme, have a hostname, and
    resolve exclusively to public IP addresses. Returns the URL unchanged.
    """
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise IngestFailure("invalid_url", f"Unsupported URL scheme '{parsed.scheme or 'none'}'.")
    host = parsed.hostname
    if not host:
        raise IngestFailure("invalid_url", "The URL does not contain a host name.")
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise IngestFailure("invalid_url", "The URL contains an invalid port.") from exc

    resolve = resolver or _default_resolver
    try:
        addresses = resolve(host, port)
    except (socket.gaierror, UnicodeError, OSError) as exc:
        raise IngestFailure("invalid_url", f"Could not resolve host '{host}'.") from exc
    if not addresses:
        raise IngestFailure("invalid_url", f"Host '{host}' did not resolve to any address.")

    for raw in addresses:
        try:
            ip = ipaddress.ip_address(raw.split("%")[0])
        except ValueError as exc:
            raise IngestFailure(
                "invalid_url", f"Host '{host}' resolved to an invalid address."
            ) from exc
        if not _is_public_address(ip):
            raise IngestFailure(
                "invalid_url",
                f"Host '{host}' resolves to a private or reserved address ({ip}); "
                "only public URLs are allowed.",
            )
    return url
