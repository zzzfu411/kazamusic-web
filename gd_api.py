"""GD音乐台 (music.gdstudio.xyz) client.

The site signs every api.php call. ``s`` is the last 8 hex chars of a
modified MD5 (standard MD5 with T[57] replaced), over:

    {unix_ms[:9]}|{hostname}|{version without dots}|{encodeURIComponent-like value}

``/time`` supplies the clock. The version string is read from player.js.
"""
from __future__ import annotations

import logging
import math
import os
import re
import struct
import threading
import time
from typing import Any
from urllib.parse import quote, urlparse

import requests

log = logging.getLogger("kazam.gd")

DEFAULT_BASE = "https://music.gdstudio.xyz"
DEFAULT_VERSION = "2026.09.25"
PLATFORMS = (
    "netease",
    "tencent",
    "kuwo",
    "migu",
    "joox",
    "tidal",
    "qobuz",
    "apple",
    "bilibili",
    "ytmusic",
)
LEVEL_BR = {
    "standard": 128,
    "higher": 192,
    "exhigh": 320,
    "lossless": 999,
    "hires": 999,
    "jymaster": 999,
}
ID_RE = re.compile(r"^gd:([a-z0-9_]+):([^/]+)$", re.I)

# Standard MD5 T-table, except round 57. That constant is the only change
# in the script shipped as js/crc32.min.js (verified against md5("abc")).
_T = [int(abs(math.sin(i + 1)) * (2**32)) & 0xFFFFFFFF for i in range(64)]
_T[57] = 0xB9302FA6
_S = (
    [7, 12, 17, 22] * 4
    + [5, 9, 14, 20] * 4
    + [4, 11, 16, 23] * 4
    + [6, 10, 15, 21] * 4
)

_lock = threading.Lock()
_version_cache: dict[str, tuple[float, str]] = {}
_track_cache: dict[str, dict[str, Any]] = {}
# base -> (fetched_at, server_ms - local_ms). The signature only uses the first
# 9 digits of the millisecond clock (10 s steps), so the offset is measured once
# and reused instead of asking /time before every call (it doubled the requests).
_clock_offset: dict[str, tuple[float, int]] = {}
_CLOCK_TTL = 15 * 60
# (base, platform, pic_id) -> (expires_at, url). Cover links are stable.
_pic_cache: dict[tuple[str, str, str], tuple[float, str]] = {}
_PIC_TTL = 24 * 3600
_PIC_TTL_MISS = 10 * 60


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
    )
    return s


def _apply_proxy(session: requests.Session) -> requests.Session:
    # Azure is blocked by GD. KAZAM_UPSTREAM_PROXY is an HTTP CONNECT proxy
    # (VPS 23) whose allowlist is just these music hosts.
    session.trust_env = False
    proxy = os.environ.get("KAZAM_UPSTREAM_PROXY", "").strip()
    if proxy:
        session.proxies.update({"http": proxy, "https": proxy})
    return session


SESSION = _apply_proxy(_session())


def js_quote(value: str) -> str:
    """Match GD urlEncode(): encodeURIComponent, then escape ! * ' ( )."""
    return quote(str(value), safe="~")


def normalize_platform(src: str | None) -> str:
    s = (src or "netease").strip().lower()
    aliases = {"qq": "tencent", "wyy": "netease", "wy": "netease", "kg": "kugou"}
    s = aliases.get(s, s)
    return s if s in PLATFORMS else "netease"


def make_sid(platform: str, native_id: str) -> str:
    return f"gd:{normalize_platform(platform)}:{native_id}"


def parse_sid(sid: str) -> tuple[str, str] | None:
    m = ID_RE.match(str(sid or "").strip())
    if not m:
        return None
    return normalize_platform(m.group(1)), m.group(2)


def _host(base: str) -> str:
    return (urlparse(base).hostname or "music.gdstudio.xyz").lower()


def _base(endpoint: str | None) -> str:
    return (endpoint or DEFAULT_BASE).rstrip("/")


def _rotl(x: int, c: int) -> int:
    x &= 0xFFFFFFFF
    return ((x << c) | (x >> (32 - c))) & 0xFFFFFFFF


def gd_md5_hex(data: bytes) -> str:
    msg = bytearray(data)
    bit_len = (len(msg) * 8) & 0xFFFFFFFFFFFFFFFF
    msg.append(0x80)
    while len(msg) % 64 != 56:
        msg.append(0)
    msg += struct.pack("<Q", bit_len)
    a0, b0, c0, d0 = 0x67452301, 0xEFCDAB89, 0x98BADCFE, 0x10325476
    for off in range(0, len(msg), 64):
        m = struct.unpack("<16I", msg[off : off + 64])
        a, b, c, d = a0, b0, c0, d0
        for i in range(64):
            if i < 16:
                f = (b & c) | (~b & d)
                g = i
            elif i < 32:
                f = (d & b) | (~d & c)
                g = (5 * i + 1) % 16
            elif i < 48:
                f = b ^ c ^ d
                g = (3 * i + 5) % 16
            else:
                f = c ^ (b | ~d)
                g = (7 * i) % 16
            f = (f + a + _T[i] + m[g]) & 0xFFFFFFFF
            a, d, c, b = d, c, b, (b + _rotl(f, _S[i])) & 0xFFFFFFFF
        a0 = (a0 + a) & 0xFFFFFFFF
        b0 = (b0 + b) & 0xFFFFFFFF
        c0 = (c0 + c) & 0xFFFFFFFF
        d0 = (d0 + d) & 0xFFFFFFFF
    return struct.pack("<4I", a0, b0, c0, d0).hex()


def _version(base: str) -> str:
    now = time.time()
    with _lock:
        hit = _version_cache.get(base)
        if hit and now - hit[0] < 6 * 3600:
            return hit[1]
    ver = DEFAULT_VERSION
    try:
        r = SESSION.get(f"{base}/js/player.js", timeout=8, headers={"Referer": base + "/"})
        m = re.search(r'version:"(\d{4}\.\d{2}\.\d{2})"', r.text or "")
        if m:
            ver = m.group(1)
    except Exception as e:
        log.debug("gd version fetch failed: %s", e)
    flat = "".join(p if len(p) >= 2 else "0" + p for p in ver.split("."))
    with _lock:
        _version_cache[base] = (now, flat)
    return flat


def _offset(base: str, refresh: bool = False) -> int:
    now = time.time()
    with _lock:
        hit = _clock_offset.get(base)
    if hit and not refresh and now - hit[0] < _CLOCK_TTL:
        return hit[1]
    offset = hit[1] if hit else 0
    try:
        t0 = time.time()
        r = SESSION.get(f"{base}/time", timeout=6, headers={"Referer": base + "/"})
        digits = re.sub(r"\D", "", (r.text or "").strip())
        if len(digits) >= 13:
            local_ms = int((t0 + time.time()) / 2 * 1000)
            offset = int(digits[:13]) - local_ms
        elif len(digits) >= 9:
            # only the signing prefix came back; align on it
            offset = int(digits[:9]) * 10000 - int(time.time() * 1000) + 5000
    except Exception as e:
        log.debug("gd /time failed: %s", e)
    with _lock:
        _clock_offset[base] = (now, offset)
    return offset


def _clock(base: str, refresh: bool = False) -> str:
    return str(int(time.time() * 1000) + _offset(base, refresh))[:9]


def sign(value: str, base: str, refresh_clock: bool = False) -> str:
    payload = f"{_clock(base, refresh_clock)}|{_host(base)}|{_version(base)}|{value}"
    return gd_md5_hex(payload.encode()).upper()[-8:]


def _get(base: str, fields: dict[str, Any], sign_key: str) -> Any:
    encoded = {k: js_quote(str(v)) for k, v in fields.items()}
    last_err: Exception | None = None
    for attempt in range(2):
        # a rejected signature usually means the clock moved: measure it again
        sig = sign(encoded[sign_key], base, refresh_clock=attempt > 0)
        qs = "&".join(f"{k}={v}" for k, v in encoded.items()) + f"&s={sig}"
        url = f"{base}/api.php?{qs}"
        try:
            r = SESSION.get(url, timeout=15, headers={"Referer": base + "/"})
            if r.status_code == 401:
                last_err = RuntimeError("gd 401")
                continue
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last_err = e
            log.warning("gd get failed: %s", e)
    raise RuntimeError(f"gd request failed: {last_err}")


def remember(sid: str, extra: dict[str, Any]) -> None:
    with _lock:
        _track_cache[sid] = extra
        if len(_track_cache) > 4000:
            for k in list(_track_cache)[:1000]:
                _track_cache.pop(k, None)


def recall(sid: str) -> dict[str, Any]:
    with _lock:
        return dict(_track_cache.get(sid) or {})


def _artists(raw: Any) -> str:
    if isinstance(raw, list):
        return " / ".join(str(x) for x in raw if x)
    return str(raw or "")


def cached_pic(base: str | None, platform: str, pic_id: str) -> str | None:
    """A cover link resolved earlier ("" = known to have none), or None."""
    key = (_base(base), normalize_platform(platform), str(pic_id))
    with _lock:
        hit = _pic_cache.get(key)
    if hit and hit[0] > time.time():
        return hit[1]
    return None


def _pic(base: str, platform: str, pic_id: str) -> str:
    if not pic_id:
        return ""
    hit = cached_pic(base, platform, pic_id)
    if hit is not None:
        return hit
    url = ""
    try:
        body = _get(
            _base(base),
            {"types": "pic", "id": pic_id, "source": normalize_platform(platform), "size": 500},
            "id",
        )
        got = (body or {}).get("url") if isinstance(body, dict) else ""
        url = got if isinstance(got, str) and got.startswith("http") else ""
    except Exception as e:
        log.debug("gd pic failed: %s", e)
        return ""  # transient: not remembered
    key = (_base(base), normalize_platform(platform), str(pic_id))
    with _lock:
        _pic_cache[key] = (time.time() + (_PIC_TTL if url else _PIC_TTL_MISS), url)
        if len(_pic_cache) > 5000:
            for k in list(_pic_cache)[:1000]:
                _pic_cache.pop(k, None)
    return url


def pic_url(pic_id: str, *, source: str, base: str | None = None) -> str:
    """Resolve a cover link (cached). Search results carry only the pic id."""
    return _pic(_base(base), source, pic_id)


def search(
    keyword: str,
    *,
    source: str = "netease",
    limit: int = 20,
    base: str | None = None,
) -> list[dict[str, Any]]:
    root = _base(base)
    platform = normalize_platform(source)
    count = max(1, min(int(limit or 20), 30))
    body = _get(
        root,
        {
            "types": "search",
            "count": count,
            "source": platform,
            "pages": 1,
            "name": keyword,
        },
        "name",
    )
    if not isinstance(body, list):
        return []
    rows = []
    for item in body[:count]:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        native = str(item["id"])
        plat = normalize_platform(str(item.get("source") or platform))
        sid = make_sid(plat, native)
        pic_id = str(item.get("pic_id") or native)
        lyric_id = str(item.get("lyric_id") or native)
        artist = _artists(item.get("artist"))
        album = str(item.get("album") or "")
        remember(
            sid,
            {
                "pic_id": pic_id,
                "lyric_id": lyric_id,
                "name": item.get("name") or "",
                "artist": artist,
                "album": album,
                "platform": plat,
            },
        )
        rows.append(
            {
                "id": sid,
                "name": item.get("name") or "",
                "artist": artist,
                "album": album,
                "pic_id": pic_id,
                "lyric_id": lyric_id,
                "platform": plat,
                "native_id": native,
                "duration": int(item.get("duration") or 0),
                # Covers are not looked up here: one signed call per row made a
                # 30-row search 60+ requests. The caller links a lazy cover
                # (see app.py /api/gd/pic) unless the link is already known.
                "picUrl": cached_pic(root, plat, pic_id) or "",
            }
        )
    return rows


def song_url(
    native_id: str,
    *,
    source: str,
    br: int = 320,
    base: str | None = None,
) -> dict[str, Any]:
    root = _base(base)
    body = _get(
        root,
        {"types": "url", "id": native_id, "source": normalize_platform(source), "br": int(br)},
        "id",
    )
    if not isinstance(body, dict):
        return {}
    url = body.get("url") or ""
    if not isinstance(url, str) or not url.startswith("http"):
        return {}
    return {
        "url": url,
        "br": body.get("br") or 0,
        "size": body.get("size") or 0,
    }


def lyric(
    lyric_id: str,
    *,
    source: str,
    base: str | None = None,
) -> dict[str, str]:
    root = _base(base)
    body = _get(
        root,
        {"types": "lyric", "id": lyric_id, "source": normalize_platform(source)},
        "id",
    )
    if not isinstance(body, dict):
        return {"lrc": "", "tlyric": ""}
    return {
        "lrc": str(body.get("lyric") or ""),
        "tlyric": str(body.get("tlyric") or ""),
    }


def br_for_level(level: str | None) -> int:
    return LEVEL_BR.get((level or "").strip().lower(), 320)


def song_meta(
    native_id: str,
    *,
    source: str,
    level: str | None = None,
    base: str | None = None,
    extra: dict | None = None,
) -> dict[str, Any]:
    platform = normalize_platform(source)
    sid = make_sid(platform, native_id)
    extra = dict(extra or {})
    extra.update({k: v for k, v in recall(sid).items() if v})
    got = song_url(native_id, source=platform, br=br_for_level(level), base=base)
    pic_id = str(extra.get("pic_id") or native_id)
    # only a cover already resolved; otherwise the caller links the lazy one
    pic = extra.get("picUrl") or cached_pic(base, platform, pic_id) or ""
    br = int(got.get("br") or 0)
    size = int(got.get("size") or 0)
    if br >= 740 or size > 20 * 1024 * 1024:
        audio_level = "lossless"
        ext = "flac"
    elif br >= 192:
        audio_level = "exhigh"
        ext = "mp3"
    else:
        audio_level = "standard"
        ext = "mp3"
    return {
        "id": sid,
        "name": extra.get("name") or "",
        "artist": extra.get("artist") or "",
        "album": extra.get("album") or "",
        "picUrl": pic or "",
        "url": got.get("url") or "",
        "br": br * 1000 if br and br < 10000 else br,
        "size": size,
        "level": audio_level,
        "type": ext,
        "platform": platform,
        "native_id": native_id,
        "pic_id": pic_id,
        "lyric_id": extra.get("lyric_id") or native_id,
        "_source": "gd",
    }


def probe(base: str | None = None, keyword: str = "晴天", platform: str = "netease") -> dict[str, Any]:
    t0 = time.time()
    checks: list[dict[str, Any]] = []
    try:
        songs = search(keyword, source=platform, limit=5, base=base)
        checks.append(
            {
                "name": "search",
                "label": "搜索",
                "ok": bool(songs),
                "ms": int((time.time() - t0) * 1000),
                "detail": songs[0]["name"] if songs else "",
                "error": "" if songs else "空结果",
            }
        )
    except Exception as e:
        songs = []
        checks.append(
            {
                "name": "search",
                "label": "搜索",
                "ok": False,
                "ms": int((time.time() - t0) * 1000),
                "detail": "",
                "error": str(e)[:160],
            }
        )
    if songs:
        t1 = time.time()
        try:
            # the first hit can be a VIP-only song: a link for any of three is a pass
            for song in songs[:3]:
                meta = song_meta(song["native_id"], source=song["platform"], base=base, extra=song)
                if meta.get("url"):
                    break
            ok = bool(meta.get("url"))
            checks.append(
                {
                    "name": "url",
                    "label": "播放地址",
                    "ok": ok,
                    "ms": int((time.time() - t1) * 1000),
                    "detail": "有直链" if ok else "",
                    "error": "" if ok else "无 url",
                }
            )
        except Exception as e:
            checks.append(
                {
                    "name": "url",
                    "label": "播放地址",
                    "ok": False,
                    "ms": int((time.time() - t1) * 1000),
                    "detail": "",
                    "error": str(e)[:160],
                }
            )
    ok_n = sum(1 for c in checks if c["ok"])
    return {
        "alive": ok_n >= 1 and any(c["name"] == "url" and c["ok"] for c in checks),
        "ok_count": ok_n,
        "total": len(checks),
        "ms": int((time.time() - t0) * 1000),
        "checks": checks,
        "summary": "可用" if ok_n >= 2 else "不可用",
    }
