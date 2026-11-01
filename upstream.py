"""
Multi-upstream NetEase helpers.
Supports admin-managed sources (kind=meting|chksz) stored in runtime.json.
No NetEase official API — only third-party proxies.
"""
from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

import requests

import config

log = logging.getLogger("kazam.upstream")

SESSION = requests.Session()
SESSION.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
)

# Env fallback only used if runtime sources empty
_ENV_METING = [
    b.rstrip("/") + "/"
    for b in config.env_list(
        "METING_APIS",
        "https://api.qijieya.cn/meting/",
    )
]


def _rt():
    import runtime_settings

    return runtime_settings


def _active_sources() -> list[dict[str, Any]]:
    try:
        return _rt().list_sources(enabled_only=True)
    except Exception:
        return [
            {
                "id": "meting_qijieya",
                "name": "Meting",
                "kind": "meting",
                "endpoint": _ENV_METING[0] if _ENV_METING else "https://api.qijieya.cn/meting/",
            }
        ]


def _source_by_id(sid: str) -> dict[str, Any] | None:
    try:
        return _rt().get_source(sid)
    except Exception:
        return None


def _meting_bases_for(provider: str | None = None) -> list[str]:
    """Resolve which meting endpoints to try for current provider."""
    p = provider or _provider()
    if p == "auto":
        bases = [
            s["endpoint"]
            for s in _active_sources()
            if s.get("kind") == "meting" and s.get("endpoint")
        ]
        return bases or list(_ENV_METING)
    src = _source_by_id(p)
    if src and src.get("kind") == "meting" and src.get("endpoint"):
        return [src["endpoint"]]
    # legacy "meting" or unknown → all enabled meting
    if p in ("meting",):
        bases = [
            s["endpoint"]
            for s in _active_sources()
            if s.get("kind") == "meting" and s.get("endpoint")
        ]
        return bases or list(_ENV_METING)
    return list(_ENV_METING)


def _chksz_endpoint(provider: str | None = None) -> str:
    p = provider or _provider()
    if p not in ("auto", "chksz"):
        src = _source_by_id(p)
        if src and src.get("kind") == "chksz":
            return str(src["endpoint"]).rstrip("/")
    # default / first chksz source
    for s in _active_sources():
        if s.get("kind") == "chksz" and s.get("endpoint"):
            return str(s["endpoint"]).rstrip("/")
    return config.UPSTREAM_API.rstrip("/")


# Back-compat name used by provider_status / older code
METING_BASES = _ENV_METING


def _id_from_meting_url(url: str) -> str | None:
    if not url:
        return None
    try:
        qs = parse_qs(urlparse(url).query)
        ids = qs.get("id") or []
        if ids:
            # pic urls sometimes use cover hash as id — song urls use numeric id
            return str(ids[0])
    except Exception:
        pass
    m = re.search(r"[?&]id=(\d{5,})", url)
    return m.group(1) if m else None


def _chksz_apikey(src: dict | None = None) -> str:
    if src:
        raw = str(src.get("apikey") or src.get("api_key") or "").strip()
        if raw:
            return raw
    return (config.CHKSZ_API_KEY or "").strip()


def chksz_get(
    path: str,
    params: dict | None = None,
    timeout: float = 8,
    base: str | None = None,
    apikey: str | None = None,
) -> dict:
    """
    ChKSz API: GET {base}/163_music|163_search|163_lyric|163_playlist
    Official host is https://api.chksz.com/api and requires ?apikey=
    """
    root = (base or _chksz_endpoint()).rstrip("/")
    url = f"{root}{path if path.startswith('/') else '/' + path}"
    q = dict(params or {})
    key = (apikey or "").strip() or _chksz_apikey()
    if key and not q.get("apikey"):
        q["apikey"] = key
    r = SESSION.get(url, params=q, timeout=timeout)
    try:
        import chksz_quota

        chksz_quota.ingest_response(r.headers, counted=True)
    except Exception:
        pass
    if r.status_code >= 400:
        # not raise_for_status(): its message carries the full URL, apikey
        # included, and ends up in the journal
        raise RuntimeError(f"chksz {path} HTTP {r.status_code}")
    try:
        return r.json()
    except Exception:
        snippet = (r.text or "")[:160]
        raise RuntimeError(f"chksz non-json: {snippet}") from None


def _looks_like_lrc(text: str) -> bool:
    """LRC often starts with [00:00.xx] — must NOT be parsed as JSON array."""
    if not text:
        return False
    s = text.lstrip()
    if re.match(r"\[\d{1,2}:\d{2}", s):
        return True
    if s.startswith("[ti:") or s.startswith("[ar:") or s.startswith("[al:"):
        return True
    # mixed: first line offset tags
    head = s[:200]
    return bool(re.search(r"\[\d{1,2}:\d{2}(?:\.\d+)?\]", head))


def meting_get(
    type_: str,
    id_: str,
    server: str = "netease",
    timeout: float = 15,
    as_json: bool = True,
    extra_params: dict | None = None,
    bases: list[str] | None = None,
) -> Any:
    last_err: Exception | None = None
    params: dict[str, Any] = {"server": server, "type": type_, "id": id_}
    if extra_params:
        params.update(extra_params)
    use_bases = bases if bases is not None else _meting_bases_for()
    if not use_bases:
        raise RuntimeError("no meting base configured")
    for base in use_bases:
        try:
            r = SESSION.get(
                base,
                params=params,
                timeout=timeout,
                allow_redirects=True,
            )
            r.raise_for_status()
            if not as_json:
                return r
            ct = (r.headers.get("content-type") or "").lower()
            text = r.text or ""
            # lyrics are plain LRC text; lines start with [mm:ss] — NEVER json.loads
            if type_ == "lrc" or "text/plain" in ct or _looks_like_lrc(text):
                return text
            if "json" in ct or (text.lstrip()[:1] in "{[" and not _looks_like_lrc(text)):
                try:
                    return r.json()
                except Exception:
                    return text
            return text
        except Exception as e:
            last_err = e
            log.warning("meting %s type=%s id=%s failed: %s", base, type_, id_, e)
    if last_err:
        raise last_err
    raise RuntimeError("no meting base configured")


def meting_audio_url(sid: str, br: int = 320, base: str | None = None) -> str:
    """Stable proxy URL that streams audio via Meting (works overseas)."""
    b = base or (_meting_bases_for()[:1] or _ENV_METING or ["https://api.qijieya.cn/meting/"])[0]
    if not b.endswith("/"):
        b += "/"
    return f"{b}?server=netease&type=url&id={sid}&br={int(br) or 320}"


def normalize_meting_item(item: dict) -> dict | None:
    """Convert Meting search/song item → internal song shape."""
    if not isinstance(item, dict):
        return None
    url = item.get("url") or ""
    sid = _id_from_meting_url(url) or item.get("id")
    if not sid:
        return None
    # duration unknown from meting search
    pic = item.get("pic") or item.get("cover") or ""
    return {
        "id": int(sid) if str(sid).isdigit() else sid,
        "name": item.get("name") or item.get("title") or "",
        "artist": item.get("artist") or "",
        "album": item.get("album") or "",
        "cover": pic,
        "duration": item.get("duration") or 0,
        "url": url or meting_audio_url(str(sid)),
        "picUrl": pic,
        "br": 0,
        "size": 0,
        "level": "meting",
    }


def _provider() -> str:
    try:
        return _rt().get_upstream()
    except Exception:
        return "meting_qijieya"


def _chksz_timeout(provider: str | None = None) -> float:
    """Short timeout in auto mode so failover is snappy."""
    p = provider or _provider()
    if p == "auto":
        return 2.5
    src = _source_by_id(p)
    if src and src.get("kind") == "chksz":
        return 10.0
    if p == "chksz":
        return 10.0
    return 2.5


def _search_chksz(
    keyword: str, limit: int, base: str | None = None, source_id: str = "chksz"
) -> list[dict]:
    body = chksz_get(
        "/163_search",
        {"keyword": keyword, "limit": limit},
        timeout=_chksz_timeout(),
        base=base,
    )
    if body.get("code") != 200:
        # quota spent, key rejected, upstream error: not "no results"
        raise RuntimeError(f"chksz search code={body.get('code')} {str(body.get('msg') or '')[:80]}")
    raw = body.get("data", {})
    if isinstance(raw, dict):
        raw = raw.get("songs", [])
    out = []
    for s in raw or []:
        if not s.get("id"):
            continue
        out.append(
            {
                "id": s.get("id"),
                "name": s.get("name", ""),
                "ar": s.get("ar") or s.get("artists"),
                "al": s.get("al") or s.get("album"),
                "artists": s.get("artists"),
                "album": s.get("album"),
                "duration": s.get("duration") or s.get("dt") or 0,
                "picUrl": (s.get("al") or {}).get("picUrl")
                if isinstance(s.get("al"), dict)
                else s.get("picUrl", ""),
                "_source": source_id,
            }
        )
    return out[:limit]


def _search_ors(keyword: str, limit: int, src: dict) -> list[dict]:
    import ors_api

    platform = src.get("platform") or "qq"
    songs = ors_api.search(
        keyword,
        source=str(platform),
        limit=limit,
        base=src.get("endpoint"),
    )
    out = []
    for s in songs:
        out.append(
            {
                "id": s["id"],
                "name": s.get("name") or "",
                "artist": s.get("artist") or "",
                "ar": [{"name": s["artist"]}] if s.get("artist") else [],
                "al": {
                    "name": s.get("album") or "",
                    "picUrl": s.get("picUrl") or "",
                },
                "duration": s.get("duration") or 0,
                "picUrl": s.get("picUrl") or "",
                "platform": s.get("platform") or platform,
                "native_id": s.get("native_id") or "",
                "hashes": s.get("hashes") or {},
                "contentId": s.get("contentId") or "",
                "_source": src.get("id") or "ors",
            }
        )
    return out


def _row_from_external(s: dict, source_id: str) -> dict:
    artist = s.get("artist") or ""
    cover = s.get("picUrl") or ""
    return {
        "id": s["id"],
        "name": s.get("name") or "",
        "artist": artist,
        "ar": [{"name": artist}] if artist else [],
        "al": {"name": s.get("album") or "", "picUrl": cover},
        "duration": s.get("duration") or 0,
        "picUrl": cover,
        "platform": s.get("platform") or "",
        "native_id": s.get("native_id") or "",
        "_source": source_id,
    }


def _src_for_kind(kind: str, platform: str | None = None) -> dict:
    sources = [s for s in _active_sources() if s.get("kind") == kind]
    if platform:
        for s in sources:
            if str(s.get("platform") or "") == platform:
                return s
    if sources:
        return sources[0]
    if kind == "gd":
        return {
            "id": "gd_netease",
            "kind": "gd",
            "endpoint": "https://music.gdstudio.xyz",
            "platform": platform or "netease",
        }
    return {
        "id": "hi_wyy",
        "kind": "hi",
        "endpoint": "https://flac.music.hi.cn",
        "platform": platform or "wyy",
    }


def gd_lazy_pic(platform: str, pic_id: str) -> str:
    """Same-origin cover link resolved on first view (app.py /api/gd/pic)."""
    if not pic_id:
        return ""
    return f"/api/gd/pic?source={quote(str(platform), safe='')}&id={quote(str(pic_id), safe='')}"


def _search_gd(keyword: str, limit: int, src: dict) -> list[dict]:
    import gd_api

    songs = gd_api.search(
        keyword,
        source=str(src.get("platform") or "netease"),
        limit=limit,
        base=src.get("endpoint"),
    )
    for s in songs:
        if not s.get("picUrl"):
            s["picUrl"] = gd_lazy_pic(s.get("platform") or "", s.get("pic_id") or "")
    return [_row_from_external(s, src.get("id") or "gd") for s in songs]


def _search_hi(keyword: str, limit: int, src: dict) -> list[dict]:
    import hiflac_api

    songs = hiflac_api.search(
        keyword,
        source=str(src.get("platform") or "kuwo"),
        limit=limit,
        base=src.get("endpoint"),
    )
    return [_row_from_external(s, src.get("id") or "hi") for s in songs]


def _meta_gd(sid: str, src: dict, level: str | None = None) -> dict:
    import gd_api

    parsed = gd_api.parse_sid(sid)
    platform = str(src.get("platform") or "netease")
    native = sid
    if parsed:
        platform, native = parsed
    meta = gd_api.song_meta(
        native,
        source=platform,
        level=level,
        base=src.get("endpoint"),
    )
    if not meta.get("picUrl"):
        meta["picUrl"] = gd_lazy_pic(meta.get("platform") or platform, meta.get("pic_id") or native)
    meta["_source"] = src.get("id") or "gd"
    return meta


def _meta_hi(sid: str, src: dict, level: str | None = None) -> dict:
    import hiflac_api

    parsed = hiflac_api.parse_sid(sid)
    platform = str(src.get("platform") or "kuwo")
    native = sid
    if parsed:
        platform, native = parsed
    meta = hiflac_api.song_meta(
        native,
        source=platform,
        level=level,
        base=src.get("endpoint"),
    )
    meta["_source"] = src.get("id") or "hi"
    return meta


def _lyric_from_gd(sid: str, src: dict) -> dict | None:
    import gd_api

    parsed = gd_api.parse_sid(sid)
    if not parsed:
        # a plain NetEase id, when a GD NetEase source is the active / fallback one
        if not (str(sid).isdigit() and _netease_capable(src)):
            return None
        parsed = ("netease", str(sid))
    platform, native = parsed
    extra = gd_api.recall(gd_api.make_sid(platform, native))
    lyric_id = str(extra.get("lyric_id") or native)
    try:
        text = gd_api.lyric(lyric_id, source=platform, base=src.get("endpoint"))
    except Exception as e:
        log.warning("gd lyric failed: %s", e)
        return None
    if not (text.get("lrc") or text.get("tlyric")):
        return None
    return {
        "lrc": text.get("lrc") or "",
        "tlyric": text.get("tlyric") or "",
        "romalrc": "",
        "klyric": "",
    }


def _lyric_from_hi(sid: str, src: dict) -> dict | None:
    import hiflac_api

    parsed = hiflac_api.parse_sid(sid)
    if not parsed:
        return None
    platform, native = parsed
    try:
        text = hiflac_api.lyric(native, source=platform, base=src.get("endpoint"))
    except Exception as e:
        log.warning("hi lyric failed: %s", e)
        return None
    if not text.get("lrc"):
        return None
    return {"lrc": text.get("lrc") or "", "tlyric": "", "romalrc": "", "klyric": ""}


def _search_meting(
    keyword: str,
    limit: int,
    bases: list[str] | None = None,
    source_id: str = "meting",
) -> list[dict]:
    data = meting_get("search", keyword, timeout=12, bases=bases)
    if not isinstance(data, list):
        return []
    songs = []
    for item in data:
        n = normalize_meting_item(item)
        if n:
            # rewrite play url to this base if provided
            if bases:
                n["url"] = meting_audio_url(str(n["id"]), base=bases[0])
            songs.append(
                {
                    "id": n["id"],
                    "name": n["name"],
                    "artist": n["artist"],
                    "ar": [{"name": n["artist"]}] if n["artist"] else [],
                    "al": {
                        "name": n.get("album") or "",
                        "picUrl": n.get("cover") or "",
                    },
                    "duration": n.get("duration") or 0,
                    "picUrl": n.get("cover") or "",
                    "_source": source_id,
                }
            )
    return songs[:limit]


# When the chosen source fails for a plain NetEase id, these kinds can answer
# the same id; tried in this order (GD is quick; Meting last, as it can return
# an HTML page or a preview clip for songs it cannot really serve).
_NETEASE_FALLBACK_ORDER = {"gd": 0, "chksz": 1, "meting": 2}


def _netease_capable(src: dict) -> bool:
    kind = src.get("kind")
    if kind == "gd":
        return str(src.get("platform") or "netease").lower() in ("netease", "wyy")
    return kind in ("chksz", "meting")


def _fallback_sources(exclude_id: str | None) -> list[dict]:
    out = [
        s for s in _active_sources()
        if s.get("id") != exclude_id and _netease_capable(s)
    ]
    out.sort(key=lambda s: _NETEASE_FALLBACK_ORDER.get(str(s.get("kind")), 9))
    return out


def _composite_route(sid: str) -> tuple[str, dict] | None:
    """(kind, source) for ids minted by GD / flac.music.hi.cn / ORS, else None."""
    import gd_api
    import hiflac_api
    import ors_api

    parsed = gd_api.parse_sid(sid)
    if parsed:
        return "gd", _src_for_kind("gd", parsed[0])
    parsed = hiflac_api.parse_sid(sid)
    if parsed:
        return "hi", _src_for_kind("hi", parsed[0])
    if ors_api.parse_sid(sid):
        return "ors", next(
            (s for s in _active_sources() if s.get("kind") == "ors"),
            {
                "id": "ors_de5",
                "kind": "ors",
                "endpoint": "https://music.ors.de5.net",
                "platform": "qq",
            },
        )
    return None


def _try_sources(op_name: str, fn_for_source) -> Any:
    """
    Run fn_for_source(src) over enabled sources in order (auto mode).
    fn returns truthy result on success.
    """
    last_err: Exception | None = None
    for src in _active_sources():
        try:
            out = fn_for_source(src)
            if out:
                return out
        except Exception as e:
            last_err = e
            log.warning("%s via %s failed: %s", op_name, src.get("id"), e)
    if last_err:
        log.warning("%s all sources failed: %s", op_name, last_err)
    return None


def search_songs(keyword: str, limit: int = 30) -> list[dict]:
    """Return list of song dicts. Respects admin upstream preference."""
    provider = _provider()

    def run_one(src: dict) -> list[dict]:
        if src.get("kind") == "chksz":
            return _search_chksz(
                keyword, limit, base=src.get("endpoint"), source_id=src["id"]
            )
        if src.get("kind") == "meting":
            return _search_meting(
                keyword,
                limit,
                bases=[src["endpoint"]],
                source_id=src["id"],
            )
        if src.get("kind") == "ors":
            return _search_ors(keyword, limit, src=src)
        if src.get("kind") == "gd":
            return _search_gd(keyword, limit, src=src)
        if src.get("kind") == "hi":
            return _search_hi(keyword, limit, src=src)
        return []

    if provider == "auto":
        out = _try_sources("search", run_one)
        return out or []

    src = _source_by_id(provider)
    if not src:
        # legacy
        if provider in ("meting", "meting_qijieya"):
            try:
                return _search_meting(keyword, limit)
            except Exception as e:
                log.warning("meting search failed: %s", e)
                return []
        return []
    try:
        # an empty list is a real answer ("nothing found"); only errors fall back
        return run_one(src) or []
    except Exception as e:
        log.warning("search via %s failed: %s", src.get("id"), e)
    for fb in _fallback_sources(src.get("id")):
        try:
            out = run_one(fb)
        except Exception as e:
            log.warning("search fallback via %s failed: %s", fb.get("id"), e)
            continue
        if out:
            log.info("search %r served by fallback %s", keyword, fb.get("id"))
            return out
    return []


def _meta_chksz(sid: str, level: str, base: str | None = None, source_id: str = "chksz") -> dict:
    body = chksz_get(
        "/163_music",
        {"id": sid, "level": level},
        timeout=_chksz_timeout(),
        base=base,
    )
    if body.get("code") != 200:
        return {}
    raw = body.get("data")
    if isinstance(raw, list):
        raw = raw[0] if raw else {}
    if not raw:
        return {}
    raw = dict(raw)
    raw["_source"] = source_id
    if not raw.get("level"):
        raw["level"] = level
    return raw


def _resolve_audio_url(play_url: str, timeout: float = 12) -> tuple[str, dict[str, str]]:
    """
    Meting type=url usually 302 → NetEase CDN. Follow redirects (no body) and
    return final URL + response headers from the last hop.
    """
    if not play_url:
        return "", {}
    try:
        # First: no body, capture Location chain
        r = SESSION.head(play_url, timeout=timeout, allow_redirects=True)
        # Some CDNs/proxies reject HEAD → fall back to Range GET
        if r.status_code >= 400 or (
            r.status_code == 200
            and "text/html" in (r.headers.get("content-type") or "").lower()
            and not (r.headers.get("content-length") or "").isdigit()
        ):
            r.close()
            r = SESSION.get(
                play_url,
                headers={"Range": "bytes=0-0"},
                timeout=timeout,
                allow_redirects=True,
                stream=True,
            )
        final = r.url or play_url
        headers = {k.lower(): v for k, v in r.headers.items()}
        try:
            r.close()
        except Exception:
            pass
        # If still on meting proxy with empty/html, try explicit no-follow Location
        if "meting" in final or "qijieya" in final:
            try:
                r2 = SESSION.get(
                    play_url, timeout=timeout, allow_redirects=False, stream=True
                )
                loc = r2.headers.get("Location") or r2.headers.get("location")
                try:
                    r2.close()
                except Exception:
                    pass
                if loc and loc.startswith("http"):
                    r3 = SESSION.head(loc, timeout=timeout, allow_redirects=True)
                    if r3.status_code >= 400:
                        r3.close()
                        r3 = SESSION.get(
                            loc,
                            headers={"Range": "bytes=0-0"},
                            timeout=timeout,
                            allow_redirects=True,
                            stream=True,
                        )
                    final = r3.url or loc
                    headers = {k.lower(): v for k, v in r3.headers.items()}
                    try:
                        r3.close()
                    except Exception:
                        pass
            except Exception as e:
                log.debug("resolve redirect failed: %s", e)
        return final, headers
    except Exception as e:
        log.debug("resolve audio url failed: %s", e)
        return play_url, {}


def _probe_audio_meta(play_url: str, requested_br: int = 320) -> dict[str, Any]:
    """
    Meting song JSON has no br/size/level. Resolve 302 → CDN and read headers.
    Fall back to the br we requested from the Meting API (default 320).
    """
    info: dict[str, Any] = {
        "size": 0,
        "br": int(requested_br or 0) * 1000 if requested_br else 0,
        "type": "mp3",
        "level": "exhigh",
        "final_url": "",
    }
    if not play_url:
        return info

    # Map requested kbps → UI level
    rb = int(requested_br or 320)
    if rb >= 999:
        info["level"] = "lossless"
        info["type"] = "flac"
    elif rb >= 320:
        info["level"] = "exhigh"
    elif rb >= 192:
        info["level"] = "higher"
    else:
        info["level"] = "standard"
    info["br"] = rb * 1000

    final, headers = _resolve_audio_url(play_url)
    info["final_url"] = final or ""
    if not headers:
        return info

    ct = (headers.get("content-type") or "").lower()
    # Meting answers songs it cannot serve (VIP) with an HTML page, status 200
    if ct.startswith("text/") or "json" in ct:
        info["playable"] = False
        return info
    cr = headers.get("content-range") or ""
    m = re.search(r"/(\d+)\s*$", cr)
    if m:
        info["size"] = int(m.group(1))
    else:
        cl = headers.get("content-length") or ""
        # ignore 1-byte range probes without total
        if cl.isdigit() and int(cl) > 1024:
            info["size"] = int(cl)

    if "flac" in ct or (final and final.lower().endswith(".flac")):
        info["type"] = "flac"
        if info["level"] not in ("lossless", "hires"):
            info["level"] = "lossless"
    elif "mp4" in ct or "m4a" in ct or "aac" in ct:
        info["type"] = "m4a"
    elif "mpeg" in ct or "mp3" in ct or "audio" in ct:
        info["type"] = "mp3"

    return info


def _estimate_br(size: int, duration_ms: int) -> int:
    if size <= 0 or duration_ms <= 0:
        return 0
    # bits/sec
    return int(size * 8 / (duration_ms / 1000.0))


def _meta_meting(
    sid: str,
    duration_ms: int = 0,
    br_kbps: int = 320,
    base: str | None = None,
    source_id: str = "meting",
) -> dict:
    name = artist = pic = lrc_url = ""
    br_kbps = int(br_kbps or 320)
    bases = [base] if base else None
    play_url = meting_audio_url(sid, br=br_kbps, base=base)
    try:
        data = meting_get("song", sid, timeout=12, bases=bases)
        if isinstance(data, list) and data:
            item = data[0]
            name = item.get("name") or ""
            artist = item.get("artist") or ""
            pic = item.get("pic") or ""
            lrc_url = item.get("lrc") or ""
            raw_url = item.get("url") or ""
            if raw_url and "br=" not in raw_url:
                sep = "&" if "?" in raw_url else "?"
                play_url = f"{raw_url}{sep}br={br_kbps}"
            elif raw_url:
                play_url = raw_url
            # Prefer our base's url endpoint for stability
            if base and "type=url" not in play_url:
                play_url = meting_audio_url(sid, br=br_kbps, base=base)
    except Exception as e:
        log.warning("meting song failed (%s): %s", source_id, e)

    probe = _probe_audio_meta(play_url, requested_br=br_kbps)
    if probe.get("playable") is False:
        play_url = ""
    size = int(probe.get("size") or 0)
    br = int(probe.get("br") or 0)
    if size and duration_ms:
        est = _estimate_br(size, duration_ms)
        if est > 0:
            br = est
    level = probe.get("level") or "exhigh"
    if br >= 900000 or (size > 20 * 1024 * 1024 and not duration_ms):
        level = "lossless"
    elif br >= 300000:
        level = "exhigh"
    elif br >= 180000:
        level = "higher"
    elif br >= 100000:
        level = "standard"

    return {
        "id": int(sid) if sid.isdigit() else sid,
        "name": name,
        "artist": artist,
        "picUrl": pic,
        "url": play_url,
        "br": br,
        "size": size,
        "level": level,
        "type": probe.get("type") or "mp3",
        "md5": "",
        "_lrc_url": lrc_url,
        "_source": source_id,
    }


def _meta_ors(
    sid: str,
    src: dict,
    level: str | None = None,
    extra: dict | None = None,
) -> dict:
    import ors_api

    # quality: flac / 320k / hires / master
    q_map = {
        "jymaster": "master",
        "hires": "hires",
        "lossless": "flac",
        "exhigh": "320k",
        "higher": "320k",
        "standard": "320k",
    }
    quality = q_map.get((level or "").lower(), "320k")
    parsed = ors_api.parse_sid(sid)
    platform = src.get("platform") or "qq"
    native = sid
    if parsed:
        platform, native = parsed
    extra = extra or {}
    try:
        return ors_api.song_meta(
            native,
            source=str(platform),
            br=quality,
            name=str(extra.get("name") or ""),
            artist=str(extra.get("artist") or ""),
            album=str(extra.get("album") or ""),
            interval=str(extra.get("interval") or ""),
            duration_ms=int(extra.get("duration") or 0),
            hashes=extra.get("hashes") if isinstance(extra.get("hashes"), dict) else None,
            main_hash=str(extra.get("mainHash") or ""),
            album_id=str(extra.get("albumId") or ""),
            base=src.get("endpoint"),
        )
    except Exception as e:
        log.warning("ors meta failed: %s", e)
        return {
            "id": ors_api.make_sid(platform, native) if native else sid,
            "url": "",
            "br": 0,
            "size": 0,
            "level": "ors",
            "_source": src.get("id") or "ors",
            "platform": platform,
            "native_id": native,
        }


def get_song_meta(sid: str, level: str | None = None) -> dict:
    """
    Return meta with playable url when possible.
    Shape compatible with old chksz /163_music data object.
    """
    level = level or config.AUDIO_LEVEL
    sid = str(sid)
    provider = _provider()

    def run_one(src: dict) -> dict:
        if src.get("kind") == "chksz":
            return _meta_chksz(
                sid, level, base=src.get("endpoint"), source_id=src["id"]
            )
        if src.get("kind") == "meting":
            return _meta_meting(
                sid, base=src.get("endpoint"), source_id=src["id"]
            )
        if src.get("kind") == "ors":
            return _meta_ors(sid, src=src, level=level)
        if src.get("kind") == "gd":
            return _meta_gd(sid, src=src, level=level)
        if src.get("kind") == "hi":
            return _meta_hi(sid, src=src, level=level)
        return {}

    # Composite ids stay on the source that minted them. A failure there is
    # final: handing "gd:…" to chksz would only spend quota on a bad id.
    route = _composite_route(sid)
    if route is not None:
        try:
            return run_one(route[1]) or {}
        except Exception as e:
            log.warning("song meta for %s via %s failed: %s", sid, route[1].get("id"), e)
            return {}
    if not sid.isdigit():
        return {}

    def attempt(src: dict) -> dict:
        try:
            raw = run_one(src) or {}
        except Exception as e:
            log.warning("song meta via %s failed: %s", src.get("id"), e)
            return {}
        if raw.get("url"):
            raw = dict(raw)
            raw["id"] = int(sid)  # GD answers as "gd:netease:<id>"; keep the asked id
        return raw

    if provider == "auto":
        # only sources that understand NetEase ids (ORS would read it as a QQ id)
        candidates = [s for s in _active_sources() if _netease_capable(s)]
    else:
        src = _source_by_id(provider)
        if not src:
            return _meta_meting(sid)
        candidates = [src] + _fallback_sources(src.get("id"))
    first: dict = {}
    for i, src in enumerate(candidates):
        raw = attempt(src)
        if raw.get("url"):
            if i and provider != "auto":
                log.info("song %s served by fallback %s", sid, src.get("id"))
            return raw
        if i == 0:
            first = raw  # names/cover from the chosen source, even without a link
    return first


def enrich_meta_duration(meta: dict, duration_ms: int) -> dict:
    """Fill/refine br from size+duration (meting often only has requested br)."""
    if not meta or not duration_ms:
        return meta
    size = int(meta.get("size") or 0)
    if not size:
        return meta
    meta = dict(meta)
    est = _estimate_br(size, duration_ms)
    if est <= 0:
        return meta
    # Prefer measured estimate over the default requested br
    meta["br"] = est
    if est >= 900000:
        meta["level"] = "lossless"
    elif est >= 300000:
        meta["level"] = "exhigh"
    elif est >= 180000:
        meta["level"] = "higher"
    elif est >= 100000:
        meta["level"] = "standard"
    return meta


def _lyric_from_ors(sid: str, src: dict) -> dict | None:
    """Prefer lrc from /lx/api/ parse; fallback /lyric?url=."""
    import ors_api

    try:
        meta = _meta_ors(sid, src=src)
        text = (meta.get("lrc") or "").strip()
        if text:
            return {"lrc": text, "tlyric": "", "romalrc": "", "klyric": ""}
        play = meta.get("url") or ""
        if play:
            text = ors_api.get_lyric_by_url(play, base=src.get("endpoint"))
            if text and text.strip():
                return {"lrc": text, "tlyric": "", "romalrc": "", "klyric": ""}
    except Exception as e:
        log.warning("ors lyric failed: %s", e)
    return None


def _lyric_from_chksz(sid: str, base: str | None = None) -> dict | None:
    body = chksz_get(
        "/163_lyric", {"id": sid}, timeout=_chksz_timeout(), base=base
    )
    if body.get("code") != 200:
        return None
    d = body.get("data") or {}
    return {
        "lrc": d.get("lrc", "") or "",
        "tlyric": d.get("tlyric", "") or "",
        "romalrc": d.get("romalrc", "") or "",
        "klyric": d.get("klyric", "") or "",
    }


def _lyric_from_meting(sid: str, bases: list[str] | None = None) -> dict | None:
    text = meting_get("lrc", sid, timeout=12, as_json=True, bases=bases)
    if isinstance(text, list):
        text = text[0] if text else ""
        if isinstance(text, dict):
            text = text.get("lrc") or text.get("lyric") or ""
    if isinstance(text, dict):
        return {
            "lrc": str(text.get("lrc") or text.get("lyric") or ""),
            "tlyric": str(text.get("tlyric") or text.get("tlrc") or ""),
            "romalrc": "",
            "klyric": "",
        }
    if isinstance(text, str) and text.strip():
        if text.lstrip().lower().startswith("<!doctype") or text.lstrip().lower().startswith(
            "<html"
        ):
            return None
        return {"lrc": text, "tlyric": "", "romalrc": "", "klyric": ""}
    return None


def get_lyric(sid: str) -> dict:
    """Return {lrc, tlyric}."""
    sid = str(sid)
    provider = _provider()
    empty = {"lrc": "", "tlyric": "", "romalrc": "", "klyric": ""}

    def run_one(src: dict) -> dict | None:
        if src.get("kind") == "chksz":
            return _lyric_from_chksz(sid, base=src.get("endpoint"))
        if src.get("kind") == "meting":
            return _lyric_from_meting(sid, bases=[src["endpoint"]])
        if src.get("kind") == "ors":
            return _lyric_from_ors(sid, src=src)
        if src.get("kind") == "gd":
            return _lyric_from_gd(sid, src=src)
        if src.get("kind") == "hi":
            return _lyric_from_hi(sid, src=src)
        return None

    route = _composite_route(sid)
    if route is not None:
        try:
            return run_one(route[1]) or empty
        except Exception as e:
            log.warning("lyric for %s failed: %s", sid, e)
            return empty
    if not sid.isdigit():
        return empty

    if provider == "auto":
        candidates = [s for s in _active_sources() if _netease_capable(s)]
    else:
        src = _source_by_id(provider)
        if not src:
            try:
                return _lyric_from_meting(sid) or empty
            except Exception:
                return empty
        candidates = [src] + _fallback_sources(src.get("id"))
    for src in candidates:
        try:
            d = run_one(src)
        except Exception as e:
            log.warning("lyric via %s failed: %s", src.get("id"), e)
            continue
        if d and (d.get("lrc") or d.get("tlyric")):
            return d
    return empty


def get_playlist_tracks(pid: str) -> list[dict]:
    pid = str(pid)
    provider = _provider()

    def from_chksz(base: str | None = None) -> list[dict]:
        body = chksz_get(
            "/163_playlist",
            {"id": pid},
            timeout=max(8, _chksz_timeout()),
            base=base,
        )
        tracks: list[Any] = []
        data = body.get("data")
        if isinstance(data, dict):
            tracks = data.get("tracks", []) or []
        elif isinstance(data, list):
            tracks = data
        elif isinstance(body, dict) and "tracks" in body:
            tracks = body["tracks"]
        return [t for t in tracks if t.get("id")]

    def from_meting(bases: list[str] | None = None, source_id: str = "meting") -> list[dict]:
        data = meting_get("playlist", pid, timeout=20, bases=bases)
        if not isinstance(data, list):
            return []
        out = []
        for item in data:
            n = normalize_meting_item(item)
            if not n:
                continue
            out.append(
                {
                    "id": n["id"],
                    "name": n["name"],
                    "ar": [{"name": n["artist"]}] if n["artist"] else [],
                    "al": {
                        "name": n.get("album") or "",
                        "picUrl": n.get("cover") or "",
                    },
                    "duration": n.get("duration") or 0,
                    "picUrl": n.get("cover") or "",
                    "_source": source_id,
                }
            )
        return out

    def run_one(src: dict) -> list[dict]:
        if src.get("kind") == "chksz":
            return from_chksz(base=src.get("endpoint"))
        if src.get("kind") == "meting":
            return from_meting(bases=[src["endpoint"]], source_id=src["id"])
        return []

    if provider == "auto":
        return _try_sources("playlist", run_one) or []

    src = _source_by_id(provider)
    if not src:
        try:
            return from_meting()
        except Exception:
            return []
    # NetEase playlists come only from chksz / Meting: with GD / ORS / hi chosen
    # (or the chosen one failing) the others are asked in turn.
    candidates = [src] + [
        s for s in _active_sources()
        if s.get("id") != src.get("id") and s.get("kind") in ("chksz", "meting")
    ]
    for one in candidates:
        try:
            out = run_one(one)
        except Exception as e:
            log.warning("playlist via %s failed: %s", one.get("id"), e)
            continue
        if out:
            return out
    return []


def provider_status() -> dict[str, Any]:
    """Snapshot for admin UI."""
    cur = _rt().get_upstream()
    return {
        "current": cur,
        "providers": _rt().provider_list_for_ui(),
        "catalog": _rt().catalog_for_ui(),
    }


def _timed(fn, timeout_note: str = "") -> dict[str, Any]:
    """Run fn(), return {ok, ms, detail, error}."""
    import time

    t0 = time.perf_counter()
    try:
        detail = fn()
        ms = int((time.perf_counter() - t0) * 1000)
        return {"ok": True, "ms": ms, "detail": detail or "", "error": ""}
    except Exception as e:
        ms = int((time.perf_counter() - t0) * 1000)
        err = str(e)[:200]
        if timeout_note and ("timed out" in err.lower() or "timeout" in err.lower()):
            err = f"{timeout_note}: {err}"
        return {"ok": False, "ms": ms, "detail": "", "error": err}


def _probe_finish(src: dict, checks: list[dict[str, Any]], min_ok: int = 2) -> dict[str, Any]:
    ok_n = sum(1 for c in checks if c.get("ok"))
    alive = ok_n >= min_ok
    ms_vals = [c["ms"] for c in checks if c.get("ok")]
    headline = next(
        (c["ms"] for c in checks if c["name"] in ("song", "url") and c.get("ok")),
        None,
    )
    return {
        "id": src.get("id"),
        "name": src.get("name") or src.get("id"),
        "kind": src.get("kind"),
        "endpoint": src.get("endpoint") or "",
        "alive": alive,
        "ok_count": ok_n,
        "total": len(checks),
        "ms": headline
        if headline is not None
        else (min(ms_vals) if ms_vals else max((c["ms"] for c in checks), default=0)),
        "checks": checks,
        "summary": "可用" if alive else "不可用/超时",
    }


def probe_source(
    src: dict[str, Any],
    sid: str = "416892104",
    keyword: str = "晴天",
) -> dict[str, Any]:
    """Live-check one configured source (chksz or meting)."""
    kind = src.get("kind")
    base = src.get("endpoint") or ""
    checks: list[dict[str, Any]] = []

    if kind in ("gd", "hi"):
        mod = __import__("gd_api" if kind == "gd" else "hiflac_api")
        platform = str(src.get("platform") or ("netease" if kind == "gd" else "wyy"))

        def do_probe():
            return mod.probe(base=base or None, keyword=keyword or "晴天", platform=platform)

        try:
            pr = do_probe()
            return {
                "id": src.get("id"),
                "name": src.get("name") or src.get("id"),
                "kind": kind,
                "endpoint": base,
                "alive": bool(pr.get("alive")),
                "ok_count": pr.get("ok_count") or 0,
                "total": pr.get("total") or 0,
                "ms": pr.get("ms") or 0,
                "checks": pr.get("checks") or [],
                "summary": pr.get("summary") or "",
            }
        except Exception as e:
            return {
                "id": src.get("id"),
                "name": src.get("name"),
                "kind": kind,
                "endpoint": base,
                "alive": False,
                "ok_count": 0,
                "total": 1,
                "ms": 0,
                "checks": [
                    {
                        "name": "probe",
                        "label": "探测",
                        "ok": False,
                        "ms": 0,
                        "detail": "",
                        "error": str(e)[:160],
                    }
                ],
                "summary": f"探测失败: {e}",
            }

    if kind == "ors":
        import ors_api

        def do_ors_probe():
            return ors_api.probe(base=base or None, keyword=keyword or "jay")

        try:
            pr = do_ors_probe()
            return {
                "id": src.get("id"),
                "name": src.get("name") or src.get("id"),
                "kind": "ors",
                "endpoint": base,
                "alive": bool(pr.get("alive")),
                "ok_count": pr.get("ok_count") or 0,
                "total": pr.get("total") or 0,
                "ms": pr.get("ms") or 0,
                "checks": pr.get("checks") or [],
                "summary": pr.get("summary") or "",
            }
        except Exception as e:
            return {
                "id": src.get("id"),
                "name": src.get("name"),
                "kind": "ors",
                "endpoint": base,
                "alive": False,
                "ok_count": 0,
                "total": 1,
                "ms": 0,
                "checks": [
                    {
                        "name": "probe",
                        "label": "探测",
                        "ok": False,
                        "ms": 0,
                        "detail": "",
                        "error": str(e)[:160],
                    }
                ],
                "summary": f"探测失败: {e}",
            }

    if kind == "chksz":
        def do_search():
            body = chksz_get(
                "/163_search",
                {"keyword": keyword, "limit": 3},
                timeout=3.5,
                base=base,
            )
            code = body.get("code")
            raw = body.get("data") or {}
            n = 0
            if isinstance(raw, dict):
                n = len(raw.get("songs") or [])
            elif isinstance(raw, list):
                n = len(raw)
            if code != 200 and n == 0:
                raise RuntimeError(f"code={code} empty")
            return f"code={code} songs={n}"

        def do_song():
            body = chksz_get(
                "/163_music",
                {"id": sid, "level": config.AUDIO_LEVEL},
                timeout=4,
                base=base,
            )
            if body.get("code") != 200:
                raise RuntimeError(f"code={body.get('code')}")
            raw = body.get("data")
            if isinstance(raw, list):
                raw = raw[0] if raw else {}
            url = (raw or {}).get("url") or ""
            br = (raw or {}).get("br") or 0
            if not url:
                raise RuntimeError("no play url")
            return f"br={br} url_ok"

        def do_lyric():
            body = chksz_get("/163_lyric", {"id": sid}, timeout=3.5, base=base)
            if body.get("code") != 200:
                raise RuntimeError(f"code={body.get('code')}")
            d = body.get("data") or {}
            n = len((d.get("lrc") or ""))
            if n < 10:
                raise RuntimeError("empty lrc")
            return f"lrc_len={n}"

        checks.append({"name": "search", "label": "搜索", **_timed(do_search, "搜索超时")})
        checks.append({"name": "song", "label": "取链", **_timed(do_song, "取链超时")})
        checks.append({"name": "lyric", "label": "歌词", **_timed(do_lyric, "歌词超时")})
        return _probe_finish(src, checks, min_ok=2)

    # meting
    bases = [base] if base else None

    def do_search():
        data = meting_get("search", keyword, timeout=8, bases=bases)
        if not isinstance(data, list) or not data:
            raise RuntimeError("empty/unsupported search")
        return f"songs={len(data)}"

    def do_song():
        data = meting_get("song", sid, timeout=8, bases=bases)
        if not isinstance(data, list) or not data:
            raise RuntimeError("empty song")
        item = data[0] if isinstance(data[0], dict) else {}
        name = item.get("name") or ""
        if not name:
            raise RuntimeError("no name")
        return f"name={name}"

    def do_lyric():
        text = meting_get("lrc", sid, timeout=8, as_json=True, bases=bases)
        if not isinstance(text, str) or len(text.strip()) < 10:
            raise RuntimeError("empty lrc")
        if not _looks_like_lrc(text) and "[" not in text:
            raise RuntimeError("not lrc text")
        return f"lrc_len={len(text)}"

    def do_url():
        # Prefer a free track for url probe — VIP often empty
        url = meting_audio_url(sid, br=320, base=base)
        r = SESSION.get(url, timeout=10, allow_redirects=False, stream=True)
        try:
            code = r.status_code
            loc = r.headers.get("Location") or r.headers.get("location") or ""
            ct = (r.headers.get("content-type") or "").lower()
            if code in (301, 302, 303, 307, 308) and loc.startswith("http"):
                return "302 → CDN"
            if code == 200 and ("audio" in ct or "mpeg" in ct or "octet" in ct):
                cl = r.headers.get("content-length") or "?"
                return f"200 audio cl={cl}"
            r2 = SESSION.get(url, timeout=12, allow_redirects=True, stream=True)
            try:
                ct2 = (r2.headers.get("content-type") or "").lower()
                cl2 = r2.headers.get("content-length") or "0"
                if "audio" in ct2 or "mpeg" in ct2 or (cl2.isdigit() and int(cl2) > 1000):
                    return f"stream ok size≈{cl2}"
                if r2.status_code == 200 and int(cl2 or 0) == 0:
                    raise RuntimeError("url empty (VIP/限流?)")
                raise RuntimeError(f"http={r2.status_code} ct={ct2[:30]}")
            finally:
                try:
                    r2.close()
                except Exception:
                    pass
        finally:
            try:
                r.close()
            except Exception:
                pass

    checks.append({"name": "search", "label": "搜索", **_timed(do_search, "搜索超时")})
    checks.append({"name": "song", "label": "元数据", **_timed(do_song, "元数据超时")})
    checks.append({"name": "lyric", "label": "歌词", **_timed(do_lyric, "歌词超时")})
    checks.append({"name": "url", "label": "音频链", **_timed(do_url, "取链超时")})

    # song+lyric enough; search optional (some meting forks lack it)
    critical = sum(
        1
        for c in checks
        if c["name"] in ("song", "lyric", "url") and c.get("ok")
    )
    ok_n = sum(1 for c in checks if c.get("ok"))
    result = _probe_finish(src, checks, min_ok=2)
    result["alive"] = critical >= 2 or ok_n >= 3
    result["summary"] = "可用" if result["alive"] else "不可用/超时"
    return result


# Back-compat wrappers
def probe_chksz(sid: str = "186016", keyword: str = "晴天") -> dict[str, Any]:
    src = _source_by_id("chksz") or {
        "id": "chksz",
        "name": "chksz",
        "kind": "chksz",
        "endpoint": config.UPSTREAM_API,
    }
    return probe_source(src, sid=sid, keyword=keyword)


def probe_meting(sid: str = "186016", keyword: str = "晴天") -> dict[str, Any]:
    bases = _meting_bases_for("auto")
    src = {
        "id": "meting",
        "name": "Meting",
        "kind": "meting",
        "endpoint": bases[0] if bases else (_ENV_METING[0] if _ENV_METING else ""),
    }
    return probe_source(src, sid=sid, keyword=keyword)


def probe_all(sid: str = "416892104", keyword: str = "晴天") -> dict[str, Any]:
    """
    Probe every configured source (not 'auto'). Does not change current provider.
    Default sid is a free track that usually resolves audio URL.
    """
    import time

    from concurrent.futures import ThreadPoolExecutor

    t0 = time.perf_counter()
    sources = _rt().list_sources(enabled_only=False)

    def one(src: dict) -> dict:
        if not src.get("enabled", True):
            return {
                "id": src["id"],
                "name": src.get("name"),
                "kind": src.get("kind"),
                "endpoint": src.get("endpoint"),
                "alive": False,
                "ok_count": 0,
                "total": 0,
                "ms": 0,
                "checks": [],
                "summary": "已禁用",
                "disabled": True,
            }
        try:
            return probe_source(src, sid=sid, keyword=keyword)
        except Exception as e:
            return {
                "id": src.get("id"),
                "name": src.get("name"),
                "kind": src.get("kind"),
                "endpoint": src.get("endpoint"),
                "alive": False,
                "ok_count": 0,
                "total": 0,
                "ms": 0,
                "checks": [],
                "summary": f"探测异常: {e}",
            }

    # sources are independent: probing them side by side takes as long as the
    # slowest one instead of the sum (9 sources used to take most of a minute)
    with ThreadPoolExecutor(max_workers=6, thread_name_prefix="probe") as pool:
        results = list(pool.map(one, sources))

    alive = [r for r in results if r.get("alive")]
    if len(alive) >= 2:
        best = min(alive, key=lambda x: x.get("ms") or 99999)
        auto_tip = f"{len(alive)} 个源可用；自动模式推荐较快: {best['id']} (~{best['ms']}ms)"
        auto_alive = True
    elif len(alive) == 1:
        auto_tip = f"仅 {alive[0]['id']} 可用，建议选用它或 auto"
        auto_alive = True
    else:
        auto_tip = "全部源不可用，请添加新源或检查网络"
        auto_alive = False

    return {
        "current": _rt().get_upstream(),
        "probe_ms": int((time.perf_counter() - t0) * 1000),
        "sid": sid,
        "keyword": keyword,
        "results": results,
        "auto": {
            "id": "auto",
            "name": "自动",
            "alive": auto_alive,
            "summary": auto_tip,
            "ms": min((r.get("ms") or 99999) for r in alive) if alive else 0,
        },
        "recommend": (
            alive[0]["id"]
            if len(alive) == 1
            else (
                min(alive, key=lambda x: x.get("ms") or 99999)["id"]
                if alive
                else "meting_qijieya"
            )
        ),
        "catalog": _rt().catalog_for_ui(),
    }
