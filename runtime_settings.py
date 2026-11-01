"""Persistent runtime settings (admin-tunable, survives restart)."""
from __future__ import annotations

import copy
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

# Built-in third-party sources (not NetEase official).
# Verified from JP Azure VPS on 2026-08-08:
#   - api.qijieya.cn/meting  : url/song/lrc/search OK
#   - api.injahow.cn/meting  : url/song/lrc OK (no search type)
#   - api.chksz.top          : often timeout overseas
BUILTIN_SOURCES: list[dict[str, Any]] = [
    {
        "id": "chksz",
        "name": "chksz",
        "kind": "chksz",
        "endpoint": config.UPSTREAM_API.rstrip("/"),
        "desc": "api.chksz.com — 需 CHKSZ_API_KEY，支持臻品母带",
        "builtin": True,
        "enabled": True,
    },
    {
        "id": "meting_qijieya",
        "name": "Meting · 祈杰",
        "kind": "meting",
        "endpoint": "https://api.qijieya.cn/meting/",
        "desc": "完整 Meting（搜索/歌词/音频），海外可用",
        "builtin": True,
        "enabled": True,
    },
    {
        "id": "meting_injahow",
        "name": "Meting · injahow",
        "kind": "meting",
        "endpoint": "https://api.injahow.cn/meting/",
        "desc": "官方 Meting-API 演示站（无 search 类型）",
        "builtin": True,
        "enabled": True,
    },
    {
        "id": "ors_de5",
        "name": "ORS 无损 · de5",
        "kind": "ors",
        "endpoint": "https://music.ors.de5.net",
        "desc": "QQ/酷狗/酷我/咪咕 无损解析（CF 代理；机房 IP 可能被拦）",
        "platform": "qq",
        "builtin": True,
        "enabled": True,
    },
    {
        "id": "gd_netease",
        "name": "GD音乐台 · 网易云",
        "kind": "gd",
        "endpoint": "https://music.gdstudio.xyz",
        "desc": "GD Studio 公开接口，网易云，可出无损",
        "platform": "netease",
        "builtin": True,
        "enabled": True,
    },
    {
        "id": "gd_tencent",
        "name": "GD音乐台 · QQ",
        "kind": "gd",
        "endpoint": "https://music.gdstudio.xyz",
        "desc": "GD Studio 公开接口，QQ 音乐",
        "platform": "tencent",
        "builtin": True,
        "enabled": True,
    },
    {
        "id": "gd_kuwo",
        "name": "GD音乐台 · 酷我",
        "kind": "gd",
        "endpoint": "https://music.gdstudio.xyz",
        "desc": "GD Studio 公开接口，酷我",
        "platform": "kuwo",
        "builtin": True,
        "enabled": True,
    },
    {
        "id": "hi_wyy",
        "name": "无损下载 · 网易云",
        "kind": "hi",
        "endpoint": "https://flac.music.hi.cn",
        "desc": "flac.music.hi.cn，网易云搜索 / FLAC",
        "platform": "wyy",
        "builtin": True,
        "enabled": True,
    },
    {
        "id": "hi_kuwo",
        "name": "无损下载 · 酷我",
        "kind": "hi",
        "endpoint": "https://flac.music.hi.cn",
        "desc": "flac.music.hi.cn，酷我搜索 / FLAC",
        "platform": "kuwo",
        "builtin": True,
        "enabled": True,
    },
]

# Suggested extras (not auto-enabled; admin can one-click add)
CATALOG: list[dict[str, Any]] = [
    {
        "id": "meting_qijieya",
        "name": "Meting · 祈杰",
        "kind": "meting",
        "endpoint": "https://api.qijieya.cn/meting/",
        "desc": "完整协议 + 搜索，JP VPS 实测可用",
    },
    {
        "id": "meting_injahow",
        "name": "Meting · injahow",
        "kind": "meting",
        "endpoint": "https://api.injahow.cn/meting/",
        "desc": "injahow/meting-api 公共实例",
    },
    {
        "id": "chksz",
        "name": "chksz",
        "kind": "chksz",
        "endpoint": "https://api.chksz.com/api",
        "desc": "官方文档 api.chksz.com — 需 apikey",
    },
    {
        "id": "meting_paugram_note",
        "name": "（参考）paugram netease",
        "kind": "meting",
        "endpoint": "https://api.paugram.com/netease/",
        "desc": "协议不同（非标准 Meting），仅作目录提示，不建议直接加",
        "disabled_add": True,
    },
    {
        "id": "ors_de5",
        "name": "ORS 无损 · de5",
        "kind": "ors",
        "endpoint": "https://music.ors.de5.net",
        "desc": "文档 https://music.ors.de5.net/docs — QQ/kg/kw/mg，音频走 CF",
        "platform": "qq",
    },
    {
        "id": "gd_qobuz",
        "name": "GD音乐台 · Qobuz",
        "kind": "gd",
        "endpoint": "https://music.gdstudio.xyz",
        "desc": "GD Studio，Qobuz Hi-Res。同一 endpoint 可改 platform",
        "platform": "qobuz",
    },
]

DEFAULTS: dict[str, Any] = {
    # Prefer working overseas source
    "upstream": "meting_qijieya",
    "sources": None,  # filled on first read
}

# Legacy id aliases → modern source id
_LEGACY_MAP = {
    "meting": "meting_qijieya",
    "qijieya": "meting_qijieya",
    "injahow": "meting_injahow",
}


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


def _default_sources() -> list[dict[str, Any]]:
    return [dict(s) for s in BUILTIN_SOURCES]


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
        if kind not in ("meting", "chksz", "ors", "gd", "hi"):
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
        elif kind == "gd":
            entry["platform"] = str(s.get("platform") or "netease").strip().lower() or "netease"
        elif kind == "hi":
            entry["platform"] = str(s.get("platform") or "wyy").strip().lower() or "wyy"
        cleaned.append(entry)
    # ensure builtins present (merge missing)
    for b in BUILTIN_SOURCES:
        if b["id"] not in seen:
            cleaned.append(dict(b))
            seen.add(b["id"])
    out["sources"] = cleaned

    cur = str(out.get("upstream") or "meting_qijieya").strip().lower()
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


# The upstream code asks for the settings several times per request (provider,
# source lookup, timeouts). They change only when the admin saves, so the parsed
# file is kept and read again only when its mtime/size changes.
_parsed: dict[str, Any] = {"key": None, "data": None}


def _file_key() -> tuple | None:
    try:
        st = _PATH.stat()
    except OSError:
        return ("missing",)
    return (st.st_mtime_ns, st.st_size)


def _read() -> dict[str, Any]:
    key = _file_key()
    if key is not None and _parsed["key"] == key and _parsed["data"] is not None:
        return copy.deepcopy(_parsed["data"])
    if key == ("missing",):
        out = _migrate({})
    else:
        try:
            data = json.loads(_PATH.read_text(encoding="utf-8"))
            out = _migrate(data if isinstance(data, dict) else {})
        except Exception:
            out = _migrate({})
    _parsed["key"], _parsed["data"] = key, copy.deepcopy(out)
    return out


def _write(data: dict[str, Any]) -> None:
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _PATH.with_suffix(".tmp")
    # don't store None sources
    payload = dict(data)
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_PATH)
    _parsed["key"] = None


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
    val = str(get_all().get("upstream") or "meting_qijieya").strip().lower()
    val = _LEGACY_MAP.get(val, val)
    if val == "auto":
        return "auto"
    if get_source(val):
        return val
    return "meting_qijieya"


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
    platform: str | None = None,
) -> dict[str, Any]:
    kind = str(kind or "").strip().lower()
    if kind not in ("meting", "chksz", "ors", "gd", "hi"):
        raise ValueError("kind must be meting|chksz|ors|gd|hi")
    endpoint = _norm_endpoint(endpoint, kind)
    name = (name or "").strip() or kind
    sid = (source_id or "").strip().lower() or _slug_id(name, kind)
    sid = re.sub(r"[^a-z0-9_]+", "_", sid).strip("_")[:40]
    if not sid:
        raise ValueError("invalid id")
    if sid == "auto":
        raise ValueError("id 'auto' is reserved")
    if kind == "gd":
        platform = (platform or "netease").strip().lower() or "netease"
    elif kind == "hi":
        platform = (platform or "wyy").strip().lower() or "wyy"
    elif kind == "ors":
        platform = (platform or "qq").strip().lower() or "qq"
    else:
        platform = ""

    with _LOCK:
        data = _read()
        sources: list[dict[str, Any]] = list(data.get("sources") or [])
        for s in sources:
            if s["id"] == sid:
                raise ValueError(f"source id already exists: {sid}")
            same_ep = s.get("endpoint") == endpoint and s.get("kind") == kind
            if same_ep and kind not in ("gd", "hi", "ors"):
                raise ValueError(f"endpoint already registered as {s['id']}")
            if same_ep and kind in ("gd", "hi", "ors") and str(s.get("platform") or "") == platform:
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
            entry["platform"] = (platform or "qq").strip().lower() or "qq"
        elif kind == "gd":
            entry["platform"] = (platform or "netease").strip().lower() or "netease"
        elif kind == "hi":
            entry["platform"] = (platform or "wyy").strip().lower() or "wyy"
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
    sources = list_sources()
    # gd / hi / ors serve several platforms from one endpoint, so the platform
    # is part of what makes an entry "already added" (GD Qobuz shares GD's URL).
    existing = {
        (s["endpoint"].rstrip("/"), s.get("platform") or "") for s in sources
    }
    ids = {s["id"] for s in sources}
    out = []
    for c in CATALOG:
        ep = (c.get("endpoint") or "").rstrip("/")
        out.append(
            {
                **c,
                "already": (ep, c.get("platform") or "") in existing or c["id"] in ids,
                "disabled_add": bool(c.get("disabled_add")),
            }
        )
    return out


# Back-compat aliases used by older code
PROVIDERS = BUILTIN_SOURCES
