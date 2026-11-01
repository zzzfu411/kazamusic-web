#!/usr/bin/env python3
"""Allowlisted CONNECT proxy on VPS 23 (the yeuxark.com server).

Azure's address is blocked by GD and flac.music.hi.cn, so KAZAM sends its
requests to those sites through here (KAZAM_UPSTREAM_PROXY). It only:
  - answers the addresses in RELAY_ALLOW_FROM (the music server), closing any
    other connection before reading a byte;
  - accepts Basic auth kazam:<RELAY_TOKEN>;
  - opens CONNECT tunnels to the hosts below, on ports 80/443;
  - holds at most RELAY_MAX_CONN tunnels at once.
It runs as an unprivileged dynamic user (see kazam-relay.service).
"""
from __future__ import annotations

import base64
import hmac
import os
import select
import socket
import sys
import threading
from socketserver import StreamRequestHandler, ThreadingTCPServer

ALLOW_HOSTS = {
    "music.gdstudio.xyz",
    "music.gdstudio.org",
    "music-api-cn.gdstudio.xyz",
    "music-api-hk.gdstudio.xyz",
    "music-api-us.gdstudio.xyz",
    "flac.music.hi.cn",
}
ALLOW_PORTS = {80, 443}
TOKEN = os.environ.get("RELAY_TOKEN", "")
LISTEN = os.environ.get("RELAY_LISTEN", "0.0.0.0")
PORT = int(os.environ.get("RELAY_PORT", "8766"))
ALLOW_FROM = {a.strip() for a in os.environ.get("RELAY_ALLOW_FROM", "").split(",") if a.strip()}
SLOTS = threading.BoundedSemaphore(int(os.environ.get("RELAY_MAX_CONN", "64")))


def _authorized(header: str) -> bool:
    if not TOKEN or not header.lower().startswith("basic "):
        return False
    try:
        raw = base64.b64decode(header.split(None, 1)[1].strip()).decode()
    except Exception:
        return False
    user, _, password = raw.partition(":")
    # both compared in constant time
    return hmac.compare_digest(user.encode(), b"kazam") & hmac.compare_digest(password.encode(), TOKEN.encode())


def _reply(wfile, status: bytes, extra: bytes = b"") -> None:
    wfile.write(b"HTTP/1.1 " + status + b"\r\n" + extra + b"Content-Length: 0\r\nConnection: close\r\n\r\n")


class Handler(StreamRequestHandler):
    timeout = 30

    def handle(self) -> None:
        if ALLOW_FROM and self.client_address[0] not in ALLOW_FROM:
            return  # not the music server: closed without a word
        if not SLOTS.acquire(blocking=False):
            _reply(self.wfile, b"503 Service Unavailable")
            return
        try:
            self._handle()
        finally:
            SLOTS.release()

    def _handle(self) -> None:
        try:
            line = self.rfile.readline(8192)
        except Exception:
            return
        if not line:
            return
        headers: dict[str, str] = {}
        for _ in range(64):
            raw = self.rfile.readline(8192)
            if raw in (b"\r\n", b"\n", b""):
                break
            if b":" in raw:
                key, value = raw.split(b":", 1)
                headers[key.decode(errors="replace").strip().lower()] = value.decode(errors="replace").strip()
        if not _authorized(headers.get("proxy-authorization", "")):
            _reply(self.wfile, b"407 Proxy Authentication Required", b'Proxy-Authenticate: Basic realm="kazam"\r\n')
            return
        parts = line.decode(errors="replace").split()
        if len(parts) < 2 or parts[0].upper() != "CONNECT":
            _reply(self.wfile, b"405 Method Not Allowed")
            return
        host, _, port_s = parts[1].partition(":")
        try:
            port = int(port_s or "443")
        except ValueError:
            port = 0
        if host.lower() not in ALLOW_HOSTS or port not in ALLOW_PORTS:
            _reply(self.wfile, b"403 Forbidden")
            return
        try:
            upstream = socket.create_connection((host, port), timeout=20)
        except OSError:
            _reply(self.wfile, b"502 Bad Gateway")
            return
        self.wfile.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        self._pipe(self.connection, upstream)

    def _pipe(self, left: socket.socket, right: socket.socket) -> None:
        sockets = [left, right]
        try:
            while True:
                ready, _, _ = select.select(sockets, [], [], 120)
                if not ready:
                    return
                for sock in ready:
                    other = right if sock is left else left
                    try:
                        data = sock.recv(65536)
                    except OSError:
                        return
                    if not data:
                        return
                    other.sendall(data)
        finally:
            for sock in (left, right):
                try:
                    sock.close()
                except OSError:
                    pass


class Server(ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> None:
    if not TOKEN:
        print("RELAY_TOKEN is required", file=sys.stderr)
        sys.exit(2)
    if not ALLOW_FROM:
        print("warning: RELAY_ALLOW_FROM is empty, any address may try the token", file=sys.stderr)
    Server((LISTEN, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
