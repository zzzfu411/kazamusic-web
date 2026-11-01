"""Requests to links an upstream handed us (audio, redirects to a CDN).

Audio comes from many CDNs, so there is no host list as for covers. Instead
every hop — the link and each redirect — must be http(s) without credentials
to a name that resolves only to public addresses; redirects are followed here,
one at a time, never by requests itself.
"""
from __future__ import annotations

import ipaddress
import socket
from typing import Any
from urllib.parse import urljoin, urlparse

import requests


def public_host(host: str) -> bool:
    """Every address the name resolves to is public (no loopback, LAN, link-local, …)."""
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except OSError:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(str(info[4][0]).split("%", 1)[0])
        except ValueError:
            return False
        if not ip.is_global:
            return False
    return bool(infos)


def public_http_url(url: str) -> bool:
    try:
        u = urlparse(url)
        host = (u.hostname or "").lower().rstrip(".")
        u.port  # raises ValueError on a malformed port
    except Exception:
        return False
    if u.scheme not in ("http", "https") or not host or u.username or u.password:
        return False
    return public_host(host)


def request_checked(
    session: requests.Session, method: str, url: str, *, hops: int = 5, **kwargs: Any
) -> requests.Response | None:
    """The response at the end of the redirect chain, or None if a hop is refused."""
    kwargs.pop("allow_redirects", None)
    for _ in range(hops + 1):
        if not public_http_url(url):
            return None
        r = session.request(method, url, allow_redirects=False, **kwargs)
        location = r.headers.get("Location") if r.is_redirect else None
        if not location:
            return r
        r.close()
        url = urljoin(url, location)
    return None
