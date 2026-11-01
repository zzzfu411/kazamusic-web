"""flac.music.hi.cn client (无损音乐下载).

POST ajax.php?act=search|album|song|getUrl|getLyric
Platforms on the page: kuwo, wyy (网易云).
getUrl needs the time+sign pair returned by search. Those are kept on disk
so a later play still resolves after the process restarts.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

log = logging.getLogger("kazam.hiflac")

DEFAULT_BASE = "https://flac.music.hi.cn"
PLATFORMS = ("kuwo", "wyy")
ID_RE = re.compile(r"^hi:(kuwo|wyy):([^/]+)$", re.I)
_LOCK = threading.Lock()
_MEM: dict[str, dict[str, Any]] = {}


def _session() -> requests.Session:
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


def _apply_proxy(session: requests.Session) -> requests.Session:
    session.trust_env = False
    proxy = os.environ.get("KAZAM_UPSTREAM_PROXY", "").strip()
    if proxy:
        session.proxies.update({"http": proxy, "https": proxy})
    return session


SESSION = _apply_proxy(_session())


def normalize_platform(src: str | None) -> str:
    s = (src or "kuwo").strip().lower()
    aliases = {"netease": "wyy", "wy": "wyy", "163": "wyy", "kw": "kuwo"}
    s = aliases.get(s, s)
    return s if s in PLATFORMS else "kuwo"


def make_sid(platform: str, native_id: str) -> str:
    return f"hi:{normalize_platform(platform)}:{native_id}"


def parse_sid(sid: str) -> tuple[str, str] | None:
    m = ID_RE.match(str(sid or "").strip())
    if not m:
        return None
    return normalize_platform(m.group(1)), m.group(2)


def _base(endpoint: str | None) -> str:
    return (endpoint or DEFAULT_BASE).rstrip("/")


def _store_path() -> Path:
    try:
        import config

        return Path(config.CACHE_DIR).parent / "hi_tokens.json"
    except Exception:
        return Path("/tmp/kazam-hi-tokens.json")


def _load() -> None:
    if _MEM:
        return
    path = _store_path()
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            _MEM.update(data)
    except Exception as e:
        log.debug("hi token load failed: %s", e)


def _save() -> None:
    path = _store_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(_MEM, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except Exception as e:
        log.debug("hi token save failed: %s", e)


def remember_many(recs: dict[str, dict[str, Any]]) -> None:
    """Store the tokens of a whole search page with one write to disk."""
    if not recs:
        return
    with _LOCK:
        _load()
        for sid, rec in recs.items():
            _MEM.pop(sid, None)  # re-insert so the newest stay at the end
            _MEM[sid] = rec
        if len(_MEM) > 2000:
            for k in list(_MEM)[: len(_MEM) - 1600]:
                _MEM.pop(k, None)
        _save()


def remember(sid: str, rec: dict[str, Any]) -> None:
    remember_many({sid: rec})


def recall(sid: str) -> dict[str, Any]:
    with _LOCK:
        _load()
        raw = _MEM.get(sid) or {}
        return dict(raw)


def _post(base: str, act: str, fields: dict[str, Any]) -> dict[str, Any]:
    r = SESSION.post(
        f"{base}/ajax.php",
        params={"act": act},
        data={k: "" if v is None else str(v) for k, v in fields.items()},
        timeout=18,
        headers={
            "Referer": base + "/",
            "Origin": base,
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
        },
    )
    r.raise_for_status()
    body = r.json()
    if not isinstance(body, dict):
        raise RuntimeError("hi non-object response")
    if body.get("code") not in (0, "0", None):
        raise RuntimeError(str(body.get("msg") or "hi api error"))
    return body


def _pick_minfo(minfos: list[dict], level: str | None) -> dict | None:
    if not minfos:
        return None
    want = (level or "exhigh").strip().lower()
    flacs = [m for m in minfos if str(m.get("format") or "").lower() == "flac"]
    mp3s = [m for m in minfos if str(m.get("format") or "").lower() == "mp3"]

    def br(m: dict) -> int:
        try:
            return int(m.get("bitrate") or 0)
        except Exception:
            return 0

    if want in ("lossless", "hires", "jymaster") and flacs:
        return max(flacs, key=br)
    if want in ("exhigh", "higher"):
        for target in (320, 192):
            for m in mp3s:
                if br(m) == target:
                    return m
    if mp3s:
        return max(mp3s, key=br)
    return flacs[0] if flacs else minfos[0]


def search(
    keyword: str,
    *,
    source: str = "kuwo",
    limit: int = 20,
    page: int = 1,
    base: str | None = None,
) -> list[dict[str, Any]]:
    root = _base(base)
    platform = normalize_platform(source)
    size = max(1, min(int(limit or 20), 30))
    body = _post(
        root,
        "search",
        {"platform": platform, "keyword": keyword, "page": page, "size": size},
    )
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    items = data.get("list") or []
    out = []
    recs: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict) or item.get("id") in (None, ""):
            continue
        native = str(item["id"])
        sid = make_sid(platform, native)
        minfos = [m for m in (item.get("minfo") or []) if isinstance(m, dict)]
        rec = {
            "name": item.get("name") or "",
            "artist": item.get("artist") or "",
            "album": item.get("album_name") or "",
            "picUrl": item.get("pic_url") or "",
            "duration": int(float(item.get("duration") or 0) * 1000)
            if item.get("duration")
            else 0,
            "time": item.get("time") or "",
            "sign": item.get("sign") or "",
            "minfo": minfos,
            "platform": platform,
            "native_id": native,
            "saved_at": int(time.time()),
        }
        recs[sid] = rec
        out.append(
            {
                "id": sid,
                "name": rec["name"],
                "artist": rec["artist"],
                "album": rec["album"],
                "picUrl": rec["picUrl"],
                "duration": rec["duration"],
                "platform": platform,
                "native_id": native,
            }
        )
        if len(out) >= size:
            break
    remember_many(recs)
    return out


# A token older than this is refreshed (by searching the title again) when a
# call made with it fails; the site's own page keeps play links for an hour.
_TOKEN_STALE = 5 * 60


def _resolve_token(
    sid: str, platform: str, native: str, base: str, *, refresh: bool = False
) -> dict[str, Any]:
    rec = recall(sid)
    if rec.get("sign") and rec.get("time") and not refresh:
        return rec
    name = str(rec.get("name") or "")
    if not name:
        return rec
    try:
        query = f"{name} {rec.get('artist') or ''}".strip()
        search(query, source=platform, limit=10, base=base)
    except Exception as e:
        log.warning("hi re-search failed: %s", e)
        return rec
    return recall(sid) or rec


def _token_stale(rec: dict[str, Any]) -> bool:
    try:
        return time.time() - int(rec.get("saved_at") or 0) > _TOKEN_STALE
    except (TypeError, ValueError):
        return True


def _post_signed(
    root: str, act: str, sid: str, platform: str, native: str, rec: dict[str, Any], fields: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """POST with the stored time+sign; on failure with an old token, refresh it once."""
    def call(r: dict[str, Any]) -> dict[str, Any]:
        return _post(root, act, {**fields, "time": r.get("time") or "", "sign": r.get("sign") or ""})

    try:
        return call(rec), rec
    except Exception:
        if not _token_stale(rec):
            raise
    fresh = _resolve_token(sid, platform, native, root, refresh=True)
    if not fresh.get("sign") or fresh.get("sign") == rec.get("sign"):
        raise RuntimeError("hi token refresh failed")
    return call(fresh), fresh


def song_meta(
    native_id: str,
    *,
    source: str,
    level: str | None = None,
    base: str | None = None,
) -> dict[str, Any]:
    root = _base(base)
    platform = normalize_platform(source)
    sid = make_sid(platform, native_id)
    rec = _resolve_token(sid, platform, native_id, root)
    chosen = _pick_minfo(list(rec.get("minfo") or []), level)
    if not chosen or not rec.get("sign"):
        return {
            "id": sid,
            "name": rec.get("name") or "",
            "artist": rec.get("artist") or "",
            "picUrl": rec.get("picUrl") or "",
            "url": "",
            "br": 0,
            "size": 0,
            "level": "hi",
            "platform": platform,
            "native_id": native_id,
            "_source": "hi",
        }
    body, rec = _post_signed(
        root,
        "getUrl",
        sid,
        platform,
        native_id,
        rec,
        {
            "platform": platform,
            "songid": native_id,
            "format": chosen.get("format") or "mp3",
            "bitrate": chosen.get("bitrate") or "320",
        },
    )
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    url = data.get("url") or ""
    if not isinstance(url, str) or not url.startswith("http"):
        url = ""
    fmt = str(chosen.get("format") or "mp3").lower()
    try:
        br = int(chosen.get("bitrate") or 0)
    except Exception:
        br = 0
    audio_level = "lossless" if fmt == "flac" or br >= 740 else "exhigh" if br >= 192 else "standard"
    return {
        "id": sid,
        "name": rec.get("name") or "",
        "artist": rec.get("artist") or "",
        "album": rec.get("album") or "",
        "picUrl": rec.get("picUrl") or "",
        "url": url,
        "br": br * 1000 if br and br < 10000 else br,
        "size": 0,
        "level": audio_level,
        "type": "flac" if fmt == "flac" else "mp3",
        "platform": platform,
        "native_id": native_id,
        "_source": "hi",
    }


def lyric(native_id: str, *, source: str, base: str | None = None) -> dict[str, str]:
    root = _base(base)
    platform = normalize_platform(source)
    sid = make_sid(platform, native_id)
    rec = _resolve_token(sid, platform, native_id, root)
    if not rec.get("sign"):
        return {"lrc": "", "tlyric": ""}
    body, _rec = _post_signed(
        root,
        "getLyric",
        sid,
        platform,
        native_id,
        rec,
        {"platform": platform, "songid": native_id},
    )
    text = body.get("data") or ""
    if not isinstance(text, str):
        text = ""
    return {"lrc": text, "tlyric": ""}


def probe(base: str | None = None, keyword: str = "晴天", platform: str = "wyy") -> dict[str, Any]:
    t0 = time.time()
    checks: list[dict[str, Any]] = []
    try:
        # size=1 comes back empty from this site even when results exist
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
            for song in songs[:3]:
                meta = song_meta(song["native_id"], source=song["platform"], base=base)
                if meta.get("url"):
                    break
            host = urlparse(meta.get("url") or "").netloc
            ok = bool(meta.get("url"))
            checks.append(
                {
                    "name": "url",
                    "label": "播放地址",
                    "ok": ok,
                    "ms": int((time.time() - t1) * 1000),
                    "detail": host,
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
        "alive": any(c["name"] == "url" and c["ok"] for c in checks),
        "ok_count": ok_n,
        "total": len(checks),
        "ms": int((time.time() - t0) * 1000),
        "checks": checks,
        "summary": "可用" if ok_n >= 2 else "不可用",
    }
