"""
Client for ORS-style music-api (endpoint from env / admin source).

Protocol (relative to configured base):

  GET /search?source=qq|kg|kw|mg&keyword=&limit=
  GET /lx/api/?source=&songmid=&quality=&name=&singer=&interval=&albumName=
       kg 另需 albumId / mainHash
  GET /lyric?url=
  GET /pic?url=
  GET /play/<链接>   ← 由解析接口 url 字段返回

quality: flac | 320k | hires | master

Platforms: qq / kg / kw / mg(migu)
"""
from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import quote, urljoin

import requests

log = logging.getLogger("kazam.ors")

def _env_base() -> str:
    try:
        import config

        return (config.env("ORS_API", "") or "").rstrip("/")
    except Exception:
        return ""


DEFAULT_BASE = _env_base()
SOURCES = ("qq", "kg", "kw", "mg")
SOURCE_ALIASES = {
    "qq": "qq",
    "tencent": "qq",
    "kg": "kg",
    "kugou": "kg",
    "kw": "kw",
    "kuwo": "kw",
    "mg": "mg",
    "migu": "mg",
}
# Map our internal levels / br hints → ORS quality
QUALITY_MAP = {
    "jymaster": "master",
    "master": "master",
    "hires": "hires",
    "lossless": "flac",
    "flac": "flac",
    "exhigh": "320k",
    "higher": "320k",
    "standard": "320k",
    "320": "320k",
    "320k": "320k",
    "2000": "master",
    "999": "hires",
    "1000": "hires",
}

ID_RE = re.compile(r"^ors:([a-z]{2}):(.+)$", re.I)


def normalize_source(src: str | None) -> str:
    s = (src or "qq").strip().lower()
    return SOURCE_ALIASES.get(s, s if s in SOURCES else "qq")


def normalize_quality(q: str | int | None) -> str:
    if q is None or q == "":
        return "320k"
    key = str(q).strip().lower().replace("kbps", "").replace(" ", "")
    if key in QUALITY_MAP:
        return QUALITY_MAP[key]
    # bare numbers
    try:
        n = int(key)
        if n >= 900:
            return "master"
        if n >= 500:
            return "hires"
        if n >= 300:
            return "320k"
        return "320k"
    except Exception:
        return "320k"


def make_sid(platform: str, native_id: str) -> str:
    return f"ors:{normalize_source(platform)}:{native_id}"


def parse_sid(sid: str) -> tuple[str, str] | None:
    m = ID_RE.match(str(sid or "").strip())
    if not m:
        return None
    return normalize_source(m.group(1)), m.group(2)


def _session() -> requests.Session:
    try:
        from curl_cffi import requests as creq  # type: ignore

        return creq.Session(impersonate="chrome120")  # type: ignore[return-value]
    except Exception:
        s = requests.Session()
        s.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            }
        )
        return s


SESSION = _session()


def _base(endpoint: str | None) -> str:
    root = (endpoint or DEFAULT_BASE or "").rstrip("/")
    if not root:
        raise RuntimeError("ORS endpoint not configured (set ORS_API or add an ors source)")
    return root


def _abs_url(base: str | None, path_or_url: str) -> str:
    u = (path_or_url or "").strip()
    if not u:
        return ""
    if u.startswith("http://") or u.startswith("https://") or u.startswith("data:"):
        return u
    return urljoin(_base(base) + "/", u.lstrip("/"))


def _get_json(
    path: str,
    params: dict | None = None,
    *,
    base: str | None = None,
    timeout: float = 15,
) -> Any:
    url = urljoin(_base(base) + "/", path.lstrip("/"))
    r = SESSION.get(url, params=params or {}, timeout=timeout, allow_redirects=True)
    text = r.text or ""
    if "Just a moment" in text or (
        r.status_code == 403 and "cloudflare" in text.lower()
    ):
        raise RuntimeError(
            "Cloudflare 拦截（机房 IP 常被挡，可换线路或浏览器过验证后重试）"
        )
    if r.status_code >= 400:
        try:
            err = r.json()
            msg = err.get("msg") or err.get("message") or r.status_code
            raise RuntimeError(f"HTTP {r.status_code}: {msg}")
        except RuntimeError:
            raise
        except Exception:
            raise RuntimeError(f"HTTP {r.status_code}: {text[:120]}")
    ct = (r.headers.get("content-type") or "").lower()
    if "json" in ct or (text[:1] in "{["):
        try:
            return r.json()
        except Exception:
            return text
    return text


def search(
    keyword: str,
    *,
    source: str = "qq",
    limit: int = 30,
    base: str | None = None,
    timeout: float = 12,
) -> list[dict[str, Any]]:
    source = normalize_source(source)
    raw = _get_json(
        "/search",
        {"source": source, "keyword": keyword, "limit": int(limit) or 30},
        base=base,
        timeout=timeout,
    )
    if not isinstance(raw, dict):
        return []
    code = raw.get("code")
    if code not in (200, 0, "200", None) and not raw.get("data"):
        raise RuntimeError(str(raw.get("msg") or "search failed"))
    items = raw.get("data") or []
    if not isinstance(items, list):
        return []
    out: list[dict[str, Any]] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        plat = normalize_source(it.get("platform") or source)
        nid = str(it.get("id") or it.get("songmid") or "").strip()
        if not nid:
            continue
        dur = it.get("duration") or it.get("interval") or 0
        try:
            # docs use interval; search sample used duration in seconds
            dv = float(dur)
            dur_ms = int(dv * 1000) if dv < 10000 else int(dv)
        except Exception:
            # interval may be "04:29"
            dur_ms = _parse_interval(str(dur))
        hashes = it.get("hashes") if isinstance(it.get("hashes"), dict) else {}
        main_hash = ""
        if hashes:
            main_hash = str(
                hashes.get("main") or hashes.get("hq") or hashes.get("sq") or ""
            )
        song = {
            "id": make_sid(plat, nid),
            "native_id": nid,
            "name": it.get("name") or it.get("title") or "",
            "artist": it.get("singer") or it.get("artist") or "",
            "album": it.get("album") or it.get("albumName") or "",
            "duration": dur_ms,
            "interval": _fmt_interval(dur_ms) if dur_ms else str(dur or ""),
            "platform": plat,
            "picUrl": it.get("pic") or it.get("cover") or it.get("picUrl") or "",
            "hashes": hashes,
            "mainHash": main_hash or str(it.get("mainHash") or ""),
            "contentId": str(it.get("contentId") or ""),
            "albumId": str(it.get("albumId") or ""),
            "_source": "ors",
        }
        out.append(song)
    return out[:limit]


def _parse_interval(s: str) -> int:
    """'04:29' or '4:29' → ms."""
    m = re.match(r"^(\d+):(\d{1,2})$", s.strip())
    if not m:
        return 0
    return (int(m.group(1)) * 60 + int(m.group(2))) * 1000


def _fmt_interval(ms: int) -> str:
    if ms <= 0:
        return ""
    sec = int(ms / 1000)
    return f"{sec // 60}:{sec % 60:02d}"


def _pick_hash(hashes: dict, quality: str) -> str:
    q = normalize_quality(quality)
    if q in ("master", "hires"):
        order = ["super", "res", "sq", "hq", "main"]
    elif q == "flac":
        order = ["sq", "res", "super", "hq", "main"]
    else:
        order = ["hq", "sq", "main"]
    for k in order:
        v = hashes.get(k)
        if v:
            return str(v)
    for v in hashes.values():
        if v:
            return str(v)
    return ""


def parse_song(
    songmid: str,
    *,
    source: str = "qq",
    quality: str | int = "320k",
    name: str = "",
    singer: str = "",
    interval: str = "",
    album_name: str = "",
    album_id: str = "",
    main_hash: str = "",
    hashes: dict | None = None,
    base: str | None = None,
    timeout: float = 18,
) -> dict[str, Any]:
    """
    Official parse endpoint: GET /lx/api/
    Returns absolute play url + optional picture/lrc/ext.
    """
    source = normalize_source(source)
    quality = normalize_quality(quality)
    if not main_hash and hashes:
        main_hash = _pick_hash(hashes, quality)

    params: dict[str, Any] = {
        "source": source,
        "songmid": songmid,
        "quality": quality,
    }
    # 解析附带（文档要求）
    if name:
        params["name"] = name
    if singer:
        params["singer"] = singer
    if interval:
        params["interval"] = interval
    if album_name:
        params["albumName"] = album_name
    # kg extras
    if source == "kg":
        if album_id:
            params["albumId"] = album_id
        if main_hash:
            params["mainHash"] = main_hash

    # path is /lx/api/ per docs
    raw = _get_json("/lx/api/", params, base=base, timeout=timeout)

    if isinstance(raw, str):
        if raw.startswith("http") or raw.startswith("/play/"):
            return {
                "url": _abs_url(base, raw.strip()),
                "br": _quality_br(quality),
                "lrc": "",
                "picture": "",
                "ext": "",
                "quality": quality,
                "raw": raw,
            }
        raise RuntimeError(f"unexpected parse response: {raw[:100]}")

    if not isinstance(raw, dict):
        raise RuntimeError("parse response not object")

    # success shapes vary: top-level or nested data
    data = raw.get("data") if isinstance(raw.get("data"), dict) else raw
    code = raw.get("code")
    if code not in (200, 0, "200", None) and not (data.get("url") or raw.get("url")):
        raise RuntimeError(str(raw.get("msg") or raw.get("message") or "parse failed"))

    rel_url = (
        data.get("url")
        or raw.get("url")
        or data.get("playUrl")
        or ""
    )
    if not rel_url:
        raise RuntimeError("no url in parse response")

    play_url = _abs_url(base, str(rel_url))
    picture = data.get("picture") or raw.get("picture") or ""
    lrc = data.get("lrc") or raw.get("lrc") or raw.get("lyric") or ""
    ext = data.get("ext") or raw.get("ext") or ""

    return {
        "url": play_url,
        "br": _quality_br(quality),
        "lrc": str(lrc) if lrc else "",
        "picture": str(picture) if picture else "",
        "ext": str(ext) if ext else "",
        "quality": quality,
        "size": int(data.get("size") or raw.get("size") or 0) or 0,
        "raw": raw,
    }


# back-compat alias
def get_url(*args: Any, **kwargs: Any) -> dict[str, Any]:
    # map old kwargs
    native_id = args[0] if args else kwargs.pop("native_id", kwargs.pop("songmid", ""))
    source = kwargs.pop("source", "qq")
    br = kwargs.pop("br", kwargs.pop("quality", "320k"))
    hash_ = kwargs.pop("hash_", kwargs.pop("main_hash", ""))
    return parse_song(
        str(native_id),
        source=source,
        quality=br,
        main_hash=hash_,
        base=kwargs.get("base"),
        timeout=float(kwargs.get("timeout") or 18),
        name=str(kwargs.get("name") or ""),
        singer=str(kwargs.get("singer") or kwargs.get("artist") or ""),
        album_name=str(kwargs.get("album_name") or kwargs.get("album") or ""),
        album_id=str(kwargs.get("album_id") or ""),
        hashes=kwargs.get("hashes"),
    )


def _quality_br(quality: str) -> int:
    q = normalize_quality(quality)
    return {
        "master": 2000000,
        "hires": 1000000,
        "flac": 900000,
        "320k": 320000,
    }.get(q, 320000)


def get_lyric_by_url(
    media_url: str, *, base: str | None = None, timeout: float = 12
) -> str:
    if not media_url:
        return ""
    raw = _get_json("/lyric", {"url": media_url}, base=base, timeout=timeout)
    if isinstance(raw, str):
        return raw if _looks_lrc(raw) else ""
    if isinstance(raw, dict):
        for k in ("lrc", "lyric", "lyrics", "data"):
            v = raw.get(k)
            if isinstance(v, str) and v.strip():
                return v
            if isinstance(v, dict):
                inner = v.get("lyric") or v.get("lrc") or ""
                if inner:
                    return str(inner)
    return ""


def proxy_pic_url(cover_url: str, *, base: str | None = None) -> str:
    if not cover_url:
        return ""
    if cover_url.startswith("data:"):
        return cover_url
    return f"{_base(base)}/pic?url={quote(cover_url, safe='')}"


def _looks_lrc(text: str) -> bool:
    s = (text or "").lstrip()
    return bool(re.match(r"\[\d{1,2}:\d{2}", s) or s.startswith("[ti:"))


def song_meta(
    sid_or_native: str,
    *,
    source: str = "qq",
    br: int | str = 320,
    name: str = "",
    artist: str = "",
    album: str = "",
    interval: str = "",
    hashes: dict | None = None,
    main_hash: str = "",
    album_id: str = "",
    content_id: str = "",
    base: str | None = None,
    duration_ms: int = 0,
) -> dict[str, Any]:
    """Build internal song meta via /lx/api/ parse."""
    del content_id  # not in official parse params
    parsed = parse_sid(sid_or_native)
    if parsed:
        source, native = parsed
    else:
        native = str(sid_or_native)
        source = normalize_source(source)

    quality = normalize_quality(br)
    if not interval and duration_ms:
        interval = _fmt_interval(int(duration_ms))

    info = parse_song(
        native,
        source=source,
        quality=quality,
        name=name,
        singer=artist,
        interval=interval,
        album_name=album,
        album_id=album_id,
        main_hash=main_hash,
        hashes=hashes,
        base=base,
    )

    br_val = int(info.get("br") or _quality_br(quality))
    level = "exhigh"
    if quality in ("master",):
        level = "jymaster"
    elif quality in ("hires",):
        level = "hires"
    elif quality in ("flac",):
        level = "lossless"
    elif br_val >= 300000:
        level = "exhigh"

    pic = info.get("picture") or ""
    # data URI can be used directly as cover
    return {
        "id": make_sid(source, native),
        "name": name,
        "artist": artist,
        "album": album,
        "picUrl": pic,
        "url": info.get("url") or "",
        "br": br_val,
        "size": int(info.get("size") or 0),
        "level": level,
        "type": info.get("ext") or "",
        "duration": duration_ms,
        "platform": source,
        "native_id": native,
        "lrc": info.get("lrc") or "",
        "_source": "ors",
        "_ors_base": _base(base),
        "_ors_quality": quality,
    }


def probe(base: str | None = None, keyword: str = "jay") -> dict[str, Any]:
    """Health check for admin UI."""
    import time

    checks: list[dict[str, Any]] = []
    t0 = time.perf_counter()
    sample: dict[str, Any] = {}

    def add(name: str, label: str, fn) -> None:
        t1 = time.perf_counter()
        try:
            detail = fn()
            checks.append(
                {
                    "name": name,
                    "label": label,
                    "ok": True,
                    "ms": int((time.perf_counter() - t1) * 1000),
                    "detail": detail or "",
                    "error": "",
                }
            )
        except Exception as e:
            checks.append(
                {
                    "name": name,
                    "label": label,
                    "ok": False,
                    "ms": int((time.perf_counter() - t1) * 1000),
                    "detail": "",
                    "error": str(e)[:160],
                }
            )

    def do_search() -> str:
        nonlocal sample
        songs = search(keyword, source="qq", limit=3, base=base, timeout=10)
        if not songs:
            raise RuntimeError("empty search")
        sample = songs[0]
        return f"n={len(songs)} id={sample.get('native_id')}"

    def do_parse() -> str:
        if not sample:
            raise RuntimeError("no sample")
        info = parse_song(
            str(sample.get("native_id")),
            source=str(sample.get("platform") or "qq"),
            quality="320k",
            name=str(sample.get("name") or ""),
            singer=str(sample.get("artist") or ""),
            interval=str(sample.get("interval") or ""),
            album_name=str(sample.get("album") or ""),
            base=base,
            timeout=15,
        )
        u = info.get("url") or ""
        if "/play/" not in u and not u.startswith("http"):
            raise RuntimeError(f"bad url: {u[:60]}")
        return f"q={info.get('quality')} ext={info.get('ext')} play_ok"

    def do_lyric() -> str:
        # Prefer lrc from parse; else hit /lyric?url=
        if sample:
            try:
                info = parse_song(
                    str(sample.get("native_id")),
                    source=str(sample.get("platform") or "qq"),
                    quality="320k",
                    name=str(sample.get("name") or "x"),
                    singer=str(sample.get("artist") or "x"),
                    base=base,
                    timeout=12,
                )
                if info.get("lrc"):
                    return f"lrc_len={len(info['lrc'])}"
                if info.get("url"):
                    text = get_lyric_by_url(info["url"], base=base, timeout=8)
                    if text:
                        return f"lyric_url_len={len(text)}"
            except Exception as e:
                raise RuntimeError(str(e)[:80]) from e
        raise RuntimeError("no lyric")

    add("search", "搜索", do_search)
    add("parse", "解析 /lx/api", do_parse)
    add("lyric", "歌词", do_lyric)

    ok_n = sum(1 for c in checks if c["ok"])
    alive = ok_n >= 1
    ms = next((c["ms"] for c in checks if c["name"] == "search" and c["ok"]), 0)
    return {
        "alive": alive,
        "ok_count": ok_n,
        "total": len(checks),
        "ms": ms or max((c["ms"] for c in checks), default=0),
        "checks": checks,
        "summary": "可用" if alive else "不可用/CF拦截",
        "probe_ms": int((time.perf_counter() - t0) * 1000),
    }
