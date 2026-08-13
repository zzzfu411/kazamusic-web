"""Persistent runtime settings (admin-tunable, survives restart)."""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import config

_LOCK = threading.Lock()
_PATH = config.CACHE_DIR.parent / "runtime.json"

# No public third-party endpoints shipped in this repo.
# Add sources via admin UI or .env (UPSTREAM_API / METING_APIS).
BUILTIN_SOURCES: list[dict[str, Any]] = []
CATALOG: list[dict[str, Any]] = []

DEFAULTS: dict[str, Any] = {
    "upstream": "auto",
    "sources": None,
}

_LEGACY_MAP: dict[str, str] = {}


def _norm_endpoint(url: str, kind: str) -> str:
    url = (url or "").strip()
    if not url:
        raise ValueError("endpoint required")
    if not re.match(r"^https?://", url, re.I):
        raise ValueError("endpoint must start with http:// or https://")
    # basic SSRF guard: no localhost / private IPs as host
    host = (urlparse(url).hostname or "").lower()
    if not host:
        raise ValueError("invalid endpoint host")
    blocked = (
        "localhost",
        "127.0.0.1",
        "0.0.0.0",
        "::1",
        "169.254.",
        "10.",
        "192.168.",
        "172.16.",
    )
    if host in ("localhost", "127.0.0.1", "0.0.0.0", "::1") or host.endswith(".local"):
        raise ValueError("localhost endpoints not allowed")
    # soft private check
    if host.startswith("10.") or host.startswith("192.168.") or host.startswith("169.254."):
        raise ValueError("private IP endpoints not allowed")
    m = re.match(r"^172\.(\d+)\.", host)
    if m and 16 <= int(m.group(1)) <= 31:
        raise ValueError("private IP endpoints not allowed")

    if kind == "meting":
        if not url.endswith("/"):
            url += "/"
    else:
        url = url.rstrip("/")
    return url


def _slug_id(name: str, kind: str) -> str:
    base = re.sub(r"[^a-zA-Z0-9]+", "_", (name or kind).strip().lower()).strip("_")
    if not base:
        base = kind
    if not base.startswith(kind) and kind == "meting":
        base = f"meting_{base}"
    return base[:40]


def _sources_from_env() -> list[dict[str, Any]]:
    """Optional bootstrap from .env only — no hardcoded public hosts."""
    out: list[dict[str, Any]] = []
    chksz = (config.UPSTREAM_API or "").strip()
    if chksz:
        try:
            ep = _norm_endpoint(chksz, "chksz")
        except ValueError:
            ep = ""
        if ep:
            out.append(
                {
                    "id": "chksz",
                    "name": "chksz",
                    "kind": "chksz",
                    "endpoint": ep,
                    "desc": "from UPSTREAM_API",
                    "builtin": True,
                    "enabled": True,
                }
            )
    for i, raw in enumerate(config.env_list("METING_APIS", config.METING_APIS or "")):
        raw = (raw or "").strip()
        if not raw:
            continue
        try:
            ep = _norm_endpoint(raw, "meting")
        except ValueError:
            continue
        sid = "meting" if i == 0 else f"meting_{i + 1}"
        out.append(
            {
                "id": sid,
                "name": f"Meting {i + 1}" if i else "Meting",
                "kind": "meting",
                "endpoint": ep,
                "desc": "from METING_APIS",
                "builtin": True,
                "enabled": True,
            }
        )
    ors = (config.env("ORS_API", "") or "").strip()
    if ors:
        try:
            ep = _norm_endpoint(ors, "ors")
        except ValueError:
            ep = ""
        if ep:
            out.append(
                {
                    "id": "ors",
                    "name": "ORS",
                    "kind": "ors",
                    "endpoint": ep,
                    "desc": "from ORS_API",
                    "platform": "qq",
                    "builtin": True,
                    "enabled": True,
                }
            )
    return out


def _default_sources() -> list[dict[str, Any]]:
    return _sources_from_env() or [dict(s) for s in BUILTIN_SOURCES]


def _migrate(data: dict[str, Any]) -> dict[str, Any]:
    """Ensure sources list exists; map legacy upstream ids."""
    out = dict(DEFAULTS)
    out.update(data or {})
    sources = out.get("sources")
    if not isinstance(sources, list) or not sources:
        sources = _default_sources()
    # normalize entries
    cleaned: list[dict[str, Any]] = []
    seen: set[str] = set()
    for s in sources:
        if not isinstance(s, dict):
            continue
        sid = str(s.get("id") or "").strip()
        kind = str(s.get("kind") or "meting").strip().lower()
        if kind not in ("meting", "chksz", "ors"):
            continue
        if not sid:
            continue
        if sid in seen:
            continue
        seen.add(sid)
        ep = str(s.get("endpoint") or "").strip()
        if not ep:
            # fill from builtin
            for b in BUILTIN_SOURCES:
                if b["id"] == sid:
                    ep = b["endpoint"]
                    break
        if not ep:
            continue
        try:
            ep = _norm_endpoint(ep, kind)
        except ValueError:
            continue
        entry = {
            "id": sid,
            "name": str(s.get("name") or sid),
            "kind": kind,
            "endpoint": ep,
            "desc": str(s.get("desc") or ""),
            "builtin": bool(s.get("builtin"))
            or any(b["id"] == sid for b in BUILTIN_SOURCES),
            "enabled": s.get("enabled", True) is not False,
        }
        if kind == "ors":
            entry["platform"] = str(s.get("platform") or "qq").strip().lower() or "qq"
        cleaned.append(entry)
    # ensure builtins present (merge missing)
    for b in BUILTIN_SOURCES:
        if b["id"] not in seen:
            cleaned.append(dict(b))
            seen.add(b["id"])
    out["sources"] = cleaned

    cur = str(out.get("upstream") or "auto").strip().lower()
    cur = _LEGACY_MAP.get(cur, cur)
    valid_ids = {s["id"] for s in cleaned} | {"auto"}
    if cur not in valid_ids:
        # pick first enabled meting, else first source, else auto
        pick = next(
            (s["id"] for s in cleaned if s["kind"] == "meting" and s.get("enabled")),
            None,
        )
        cur = pick or (cleaned[0]["id"] if cleaned else "auto")
    out["upstream"] = cur
    return out


def _read() -> dict[str, Any]:
    if not _PATH.exists():
        return _migrate({})
    try:
        data = json.loads(_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return _migrate({})
        return _migrate(data)
    except Exception:
        return _migrate({})


def _write(data: dict[str, Any]) -> None:
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _PATH.with_suffix(".tmp")
    # don't store None sources
    payload = dict(data)
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_PATH)


def get_all() -> dict[str, Any]:
    with _LOCK:
        return _read()


def list_sources(enabled_only: bool = False) -> list[dict[str, Any]]:
    sources = get_all().get("sources") or []
    if enabled_only:
        return [s for s in sources if s.get("enabled", True)]
    return list(sources)


def get_source(sid: str) -> dict[str, Any] | None:
    sid = _LEGACY_MAP.get(str(sid or "").strip().lower(), str(sid or "").strip().lower())
    for s in list_sources():
        if s["id"] == sid:
            return s
    return None


def get_upstream() -> str:
    val = str(get_all().get("upstream") or "auto").strip().lower()
    val = _LEGACY_MAP.get(val, val)
    if val == "auto":
        return "auto"
    if get_source(val):
        return val
    return "auto"


def set_upstream(provider: str) -> str:
    provider = str(provider or "").strip().lower()
    provider = _LEGACY_MAP.get(provider, provider)
    if provider != "auto" and not get_source(provider):
        raise ValueError(f"unknown provider: {provider}")
    with _LOCK:
        data = _read()
        data["upstream"] = provider
        _write(data)
    return provider


def add_source(
    *,
    name: str,
    kind: str,
    endpoint: str,
    desc: str = "",
    source_id: str | None = None,
) -> dict[str, Any]:
    kind = str(kind or "").strip().lower()
    if kind not in ("meting", "chksz", "ors"):
        raise ValueError("kind must be meting|chksz|ors")
    endpoint = _norm_endpoint(endpoint, kind)
    name = (name or "").strip() or kind
    sid = (source_id or "").strip().lower() or _slug_id(name, kind)
    sid = re.sub(r"[^a-z0-9_]+", "_", sid).strip("_")[:40]
    if not sid:
        raise ValueError("invalid id")
    if sid == "auto":
        raise ValueError("id 'auto' is reserved")

    with _LOCK:
        data = _read()
        sources: list[dict[str, Any]] = list(data.get("sources") or [])
        for s in sources:
            if s["id"] == sid:
                raise ValueError(f"source id already exists: {sid}")
            if s.get("endpoint") == endpoint and s.get("kind") == kind:
                raise ValueError(f"endpoint already registered as {s['id']}")
        entry = {
            "id": sid,
            "name": name,
            "kind": kind,
            "endpoint": endpoint,
            "desc": (desc or "").strip()[:200],
            "builtin": False,
            "enabled": True,
            "added_at": int(time.time()),
        }
        if kind == "ors":
            entry["platform"] = "qq"
        sources.append(entry)
        data["sources"] = sources
        _write(data)
        return entry


def delete_source(sid: str) -> None:
    sid = str(sid or "").strip().lower()
    if sid == "auto":
        raise ValueError("cannot delete auto")
    with _LOCK:
        data = _read()
        sources: list[dict[str, Any]] = list(data.get("sources") or [])
        target = next((s for s in sources if s["id"] == sid), None)
        if not target:
            raise ValueError("source not found")
        if target.get("builtin"):
            raise ValueError("cannot delete built-in source (disable instead)")
        sources = [s for s in sources if s["id"] != sid]
        data["sources"] = sources
        if data.get("upstream") == sid:
            # fall back
            data["upstream"] = next(
                (s["id"] for s in sources if s["kind"] == "meting"),
                sources[0]["id"] if sources else "auto",
            )
        _write(data)


def set_source_enabled(sid: str, enabled: bool) -> dict[str, Any]:
    sid = str(sid or "").strip().lower()
    with _LOCK:
        data = _read()
        sources: list[dict[str, Any]] = list(data.get("sources") or [])
        found = None
        for s in sources:
            if s["id"] == sid:
                s["enabled"] = bool(enabled)
                found = dict(s)
                break
        if not found:
            raise ValueError("source not found")
        data["sources"] = sources
        if not enabled and data.get("upstream") == sid:
            data["upstream"] = "auto"
        _write(data)
        return found


def provider_list_for_ui() -> list[dict[str, Any]]:
    """Providers shown in admin: all sources + synthetic auto."""
    items = []
    for s in list_sources():
        items.append(
            {
                "id": s["id"],
                "name": s["name"],
                "kind": s["kind"],
                "desc": s.get("desc") or "",
                "endpoint": s.get("endpoint") or "",
                "platform": s.get("platform") or "",
                "builtin": bool(s.get("builtin")),
                "enabled": s.get("enabled", True) is not False,
                "deletable": not bool(s.get("builtin")),
            }
        )
    items.append(
        {
            "id": "auto",
            "name": "自动",
            "kind": "auto",
            "desc": "按列表顺序短超时尝试各启用源，第一个成功的胜出",
            "endpoint": "",
            "builtin": True,
            "enabled": True,
            "deletable": False,
        }
    )
    return items


def catalog_for_ui() -> list[dict[str, Any]]:
    existing = {s["endpoint"].rstrip("/") for s in list_sources()}
    out = []
    for c in CATALOG:
        ep = (c.get("endpoint") or "").rstrip("/")
        out.append(
            {
                **c,
                "already": ep in existing or any(
                    s["id"] == c["id"] for s in list_sources()
                ),
                "disabled_add": bool(c.get("disabled_add")),
            }
        )
    return out


# Back-compat aliases used by older code
PROVIDERS = BUILTIN_SOURCES
