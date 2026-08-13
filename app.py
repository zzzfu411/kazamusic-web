#!/usr/bin/env python3
"""KAZAM VPS — production music player for music.yeuxark.com"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import random
import re
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

import requests
from flask import Flask, Response, jsonify, render_template, request, send_from_directory

import config
import upstream

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("kazam")

app = Flask(
    __name__,
    static_folder="static",
    static_url_path="/static",
    template_folder="templates",
)
if config.TRUST_PROXY:
    from werkzeug.middleware.proxy_fix import ProxyFix

    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

# reuse upstream session (timeouts / UA already set)
SESSION = upstream.SESSION

MIME_BY_EXT = {
    "flac": "audio/flac",
    "mp3": "audio/mpeg",
    "m4a": "audio/mp4",
    "aac": "audio/aac",
    "ogg": "audio/ogg",
    "wav": "audio/wav",
}


# ── CORS ──────────────────────────────────────────────────────────
def _cors_origin() -> str | None:
    origin = request.headers.get("Origin", "")
    if not origin:
        return None
    if origin in config.CORS_ORIGINS or "*" in config.CORS_ORIGINS:
        return origin
    return None


@app.after_request
def add_cors(resp: Response) -> Response:
    origin = _cors_origin()
    if origin:
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Access-Control-Allow-Credentials"] = "true"
        resp.headers["Vary"] = "Origin"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, HEAD, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = (
        "Range, Content-Type, Accept, X-Admin-Token"
    )
    resp.headers["Access-Control-Expose-Headers"] = (
        "Content-Range, Accept-Ranges, Content-Length, X-Kazam-Cache"
    )
    return resp


@app.route("/api/<path:_any>", methods=["OPTIONS"])
@app.route("/embed", methods=["OPTIONS"])
def options_preflight(_any: str = "") -> Response:
    return Response(status=204)


# ── Admin auth ────────────────────────────────────────────────────
def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def make_admin_token(ttl: int | None = None) -> str:
    ttl = ttl if ttl is not None else config.ADMIN_TOKEN_TTL
    exp = int(time.time()) + max(60, ttl)
    payload = str(exp).encode("ascii")
    sig = hmac.new(
        config.ADMIN_SECRET.encode("utf-8"),
        payload + b"|" + config.ADMIN_PASSWORD.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return f"{_b64url(payload)}.{_b64url(sig)}"


def verify_admin_token(token: str) -> bool:
    if not token or "." not in token:
        return False
    try:
        p_b64, s_b64 = token.split(".", 1)
        payload = _b64url_decode(p_b64)
        exp = int(payload.decode("ascii"))
        if exp < int(time.time()):
            return False
        expect = hmac.new(
            config.ADMIN_SECRET.encode("utf-8"),
            payload + b"|" + config.ADMIN_PASSWORD.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        got = _b64url_decode(s_b64)
        return hmac.compare_digest(expect, got)
    except Exception:
        return False


def require_admin() -> Response | None:
    """Return error Response if not admin; None if OK."""
    token = (
        request.headers.get("X-Admin-Token")
        or request.args.get("admin_token")
        or ""
    ).strip()
    if verify_admin_token(token):
        return None
    return jsonify({"ok": False, "error": "admin required"}), 401


@app.route("/api/admin/login", methods=["POST"])
def admin_login():
    data = request.get_json(silent=True) or {}
    pwd = data.get("password") or request.form.get("password") or ""
    if not hmac.compare_digest(str(pwd), str(config.ADMIN_PASSWORD)):
        # constant-ish delay
        time.sleep(0.3)
        return jsonify({"ok": False, "error": "invalid password"}), 401
    token = make_admin_token()
    return jsonify(
        {
            "ok": True,
            "token": token,
            "expiresIn": config.ADMIN_TOKEN_TTL,
        }
    )


@app.route("/api/admin/check")
def admin_check():
    token = (request.headers.get("X-Admin-Token") or "").strip()
    ok = verify_admin_token(token)
    return jsonify({"ok": ok, "admin": ok})


@app.route("/api/admin/upstream", methods=["GET", "POST"])
def admin_upstream():
    """Admin: view/set active upstream provider (source id | auto)."""
    denied = require_admin()
    if denied:
        return denied
    import runtime_settings

    if request.method == "GET":
        st = upstream.provider_status()
        st["ok"] = True
        return jsonify(st)

    data = request.get_json(silent=True) or {}
    provider = (data.get("provider") or request.form.get("provider") or "").strip()
    try:
        cur = runtime_settings.set_upstream(provider)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    st = upstream.provider_status()
    st["ok"] = True
    st["current"] = cur
    log.info("admin set upstream=%s", cur)
    return jsonify(st)


@app.route("/api/admin/upstream/sources", methods=["POST"])
def admin_upstream_add_source():
    """Admin: add a custom third-party source (kind=meting|chksz)."""
    denied = require_admin()
    if denied:
        return denied
    import runtime_settings

    data = request.get_json(silent=True) or {}
    try:
        entry = runtime_settings.add_source(
            name=str(data.get("name") or "").strip(),
            kind=str(data.get("kind") or "meting").strip(),
            endpoint=str(data.get("endpoint") or "").strip(),
            desc=str(data.get("desc") or "").strip(),
            source_id=str(data.get("id") or "").strip() or None,
        )
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        log.exception("add source failed")
        return jsonify({"ok": False, "error": str(e)}), 500
    st = upstream.provider_status()
    st["ok"] = True
    st["added"] = entry
    log.info("admin added source id=%s endpoint=%s", entry.get("id"), entry.get("endpoint"))
    return jsonify(st)


@app.route("/api/admin/upstream/sources/<sid>", methods=["DELETE"])
def admin_upstream_del_source(sid: str):
    """Admin: delete a non-builtin source."""
    denied = require_admin()
    if denied:
        return denied
    import runtime_settings

    try:
        runtime_settings.delete_source(sid)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    st = upstream.provider_status()
    st["ok"] = True
    st["deleted"] = sid
    log.info("admin deleted source id=%s", sid)
    return jsonify(st)


@app.route("/api/admin/upstream/probe", methods=["GET", "POST"])
def admin_upstream_probe():
    """
    Admin: live-check upstream reachability + latency.
    Does NOT change the active provider — only measures.
    Optional: ?sid=416892104&keyword=晴天  (free track preferred for url test)
    """
    denied = require_admin()
    if denied:
        return denied
    data = request.get_json(silent=True) or {}
    sid = (
        request.args.get("sid")
        or data.get("sid")
        or "416892104"
    )
    keyword = (
        request.args.get("keyword")
        or data.get("keyword")
        or "晴天"
    )
    sid = re.sub(r"\D", "", str(sid))[:20] or "416892104"
    keyword = str(keyword).strip()[:40] or "晴天"
    try:
        result = upstream.probe_all(sid=sid, keyword=keyword)
        result["ok"] = True
        return jsonify(result)
    except Exception as e:
        log.exception("upstream probe failed")
        return jsonify({"ok": False, "error": str(e)}), 500


# ── Upstream helpers ──────────────────────────────────────────────
def api_get(path: str, params: dict | None = None, timeout: float = 12) -> dict:
    """Legacy chksz-only helper (kept for any remaining direct calls)."""
    return upstream.chksz_get(path, params=params, timeout=timeout)


def try_https(url: str) -> str:
    if not url or not url.startswith("http://"):
        return url
    https = "https://" + url[7:]
    try:
        r = SESSION.head(https, timeout=4, allow_redirects=True)
        if r.status_code < 500:
            return https
    except Exception:
        pass
    return url


def _extract_artists(track: dict) -> str:
    raw = track.get("ar") or track.get("artists") or track.get("artist", "")
    if isinstance(raw, list):
        return " / ".join(a["name"] if isinstance(a, dict) else str(a) for a in raw)
    return str(raw) if raw else ""


def _extract_album(track: dict) -> str:
    raw = track.get("al") or track.get("album", "")
    if isinstance(raw, dict):
        return raw.get("name", "") or ""
    return str(raw) if raw else ""


def _extract_cover(track: dict) -> str:
    al = track.get("al")
    if isinstance(al, dict) and al.get("picUrl"):
        return al["picUrl"]
    if track.get("picUrl"):
        return track["picUrl"]
    album = track.get("album")
    if isinstance(album, dict) and album.get("picUrl"):
        return album["picUrl"]
    return ""


def normalize_song(s: dict) -> dict:
    sid = s.get("id")
    return {
        "id": sid,
        "name": s.get("name", ""),
        "artist": _extract_artists(s),
        "album": _extract_album(s),
        "cover": try_https(_extract_cover(s)),
        "duration": s.get("duration", 0) or s.get("dt", 0) or 0,
        "cached": is_cached(str(sid)) if sid else False,
    }


# chksz / NetEase proxy quality ladder (ORS maps internally)
AUDIO_LEVELS = (
    "standard",
    "higher",
    "exhigh",
    "lossless",
    "hires",
    "jymaster",
)
# Prefer live CDN for these — local cache is usually 320k mp3
HIGH_AUDIO_LEVELS = frozenset({"lossless", "hires", "jymaster"})


def parse_audio_level(raw: str | None = None) -> str:
    """Normalize ?level= query; fall back to server default."""
    lv = (raw or "").strip().lower()
    if lv in AUDIO_LEVELS:
        return lv
    default = (config.AUDIO_LEVEL or "exhigh").strip().lower()
    return default if default in AUDIO_LEVELS else "exhigh"


def get_song_meta(sid: str, level: str | None = None, duration_ms: int = 0) -> dict:
    """Prefer configured upstream; enrich br from duration when possible."""
    raw = upstream.get_song_meta(sid, level=parse_audio_level(level))
    if raw and duration_ms:
        raw = upstream.enrich_meta_duration(raw, duration_ms)
    return raw


def get_audio_url(sid: str, level: str | None = None) -> str:
    raw = get_song_meta(sid, level=level)
    url = (raw.get("url") or "") if raw else ""
    if url:
        return url
    # last resort: direct meting stream endpoint
    try:
        return upstream.meting_audio_url(str(sid))
    except Exception:
        return ""


def cover_host_ok(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    if not host:
        return False
    # allow generic meting-style pic proxies + netease CDNs
    if "meting" in host:
        return True
    return any(host == s or host.endswith("." + s) for s in config.COVER_HOST_SUFFIXES)


def stream_audio(audio_url: str, range_header: str | None = None):
    parsed = urlparse(audio_url)
    ext = parsed.path.rsplit(".", 1)[-1] if "." in parsed.path else "mp3"
    content_type = MIME_BY_EXT.get(ext.lower(), "audio/mpeg")

    total_size = 0
    try:
        head = SESSION.head(audio_url, timeout=10, allow_redirects=True)
        total_size = int(head.headers.get("content-length", 0) or 0)
    except Exception:
        pass

    req_headers: dict[str, str] = {"Accept": "*/*"}
    if range_header:
        req_headers["Range"] = range_header

    def generate():
        try:
            with SESSION.get(
                audio_url, headers=req_headers, stream=True, timeout=60
            ) as resp:
                resp.raise_for_status()
                for chunk in resp.iter_content(256 * 1024):
                    if chunk:
                        yield chunk
        except Exception as e:
            log.warning("stream error: %s", e)

    return generate(), content_type, ext, total_size


def make_download_headers(filename: str, fallback: str = "download") -> dict:
    ascii_name = re.sub(r"[^a-zA-Z0-9._-]", "_", filename)[:80] or fallback
    encoded = quote(filename.encode("utf-8"), safe="")
    return {
        "Content-Disposition": (
            f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{encoded}'
        ),
        "Accept-Ranges": "bytes",
        "Cache-Control": "no-cache",
    }


def make_lyric_text(raw: dict, name: str, artist: str) -> str:
    parts = []
    if name:
        parts.append(f"[ti:{name}]")
    if artist:
        parts.append(f"[ar:{artist}]")
    lrc = (raw.get("lrc") or "").strip()
    tlrc = (raw.get("tlyric") or raw.get("tlrc") or "").strip()
    if lrc:
        parts.append(lrc)
    if tlrc:
        parts.append("\n[翻译歌词]\n" + tlrc)
    return "\n".join(parts) + "\n"


# ── Parse ID + Local cache ────────────────────────────────────────
def parse_song_id(raw: str) -> str | None:
    """Extract netease song id from bare id or share/page URL."""
    if not raw:
        return None
    s = raw.strip()
    if re.fullmatch(r"\d{5,}", s):
        return s
    patterns = [
        r"[?&#]id=(\d{5,})",
        r"/song(?:/m)?/(\d{5,})",
        r"song\?id=(\d{5,})",
        r"outchain/\d+/(\d{5,})",
        r"music\.163\.com(?:/m)?/song/(\d{5,})",
    ]
    for p in patterns:
        m = re.search(p, s, re.I)
        if m:
            return m.group(1)
    m = re.search(r"(\d{6,})", s)
    return m.group(1) if m else None


def cache_meta_path(sid: str) -> Path:
    return config.CACHE_DIR / f"{sid}.meta.json"


def find_cached_audio(sid: str) -> Path | None:
    files = [
        p
        for p in sorted(config.CACHE_DIR.glob(f"{sid}.audio.*"))
        if p.is_file() and p.stat().st_size > 1024
    ]
    return files[0] if files else None


def load_cache_meta(sid: str) -> dict | None:
    p = cache_meta_path(sid)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def is_cached(sid: str) -> bool:
    return find_cached_audio(str(sid)) is not None


def build_song_payload(
    sid: str, raw: dict | None = None, cached: bool | None = None
) -> dict:
    raw = raw or {}
    sid = str(raw.get("id") or sid)
    hit = is_cached(sid) if cached is None else cached
    meta = load_cache_meta(sid) or {}
    name = raw.get("name") or meta.get("name") or ""
    artist = raw.get("artist") or meta.get("artist") or ""
    cover = try_https(
        raw.get("picUrl") or raw.get("cover") or meta.get("cover") or ""
    )
    if hit and (config.CACHE_DIR / f"{sid}.cover.jpg").exists():
        # relative path works both on localhost and music.yeuxark.com
        cover_url = f"/api/cache/{sid}/cover"
    else:
        cover_url = cover
    src = raw.get("_source") or (
        "meting" if (raw.get("level") == "meting") else ""
    )
    return {
        "id": sid,
        "name": name,
        "artist": artist,
        "album": raw.get("album") or meta.get("album") or "",
        "cover": cover_url or cover,
        "duration": raw.get("duration") or meta.get("duration") or 0,
        "br": raw.get("br") or meta.get("br") or 0,
        "size": raw.get("size") or meta.get("size") or 0,
        "level": raw.get("level") or meta.get("level") or "",
        "source": src or raw.get("source") or "",
        "cached": hit,
        "url": raw.get("url") or "",
        "streamUrl": f"{config.PUBLIC_BASE_URL}/api/stream/{sid}",
        "pageUrl": f"{config.PUBLIC_BASE_URL}/?play={sid}",
        "embedUrl": f"{config.PUBLIC_BASE_URL}/embed?id={sid}",
        "cdnUrl": raw.get("url") or "",
    }


def cache_song(sid: str, level: str | None = None) -> dict:
    """Download audio (+ cover + lyric) into CACHE_DIR. Idempotent."""
    sid = str(sid).strip()
    level = parse_audio_level(level)
    # allow ORS composite ids (ors:qq:xxx) and plain numeric netease ids
    if not (re.fullmatch(r"\d+", sid) or re.fullmatch(r"ors:[a-z]{2}:.+", sid, re.I)):
        raise ValueError("invalid song id")

    existing = find_cached_audio(sid)
    meta_existing = load_cache_meta(sid)
    # Re-cache if requested quality is higher than what we stored
    if existing and meta_existing:
        stored = str(meta_existing.get("level") or "").lower()
        if stored == level or (
            level not in HIGH_AUDIO_LEVELS and stored not in HIGH_AUDIO_LEVELS
        ):
            return build_song_payload(sid, meta_existing, cached=True)

    raw = get_song_meta(sid, level=level)
    audio_url = (raw or {}).get("url") or ""
    if not raw or not audio_url:
        raise RuntimeError("unable to fetch audio url from upstream")

    path_ext = (
        urlparse(audio_url).path.rsplit(".", 1)[-1].lower()
        if "." in urlparse(audio_url).path
        else ""
    )
    if path_ext not in MIME_BY_EXT:
        br = int(raw.get("br") or 0)
        typ = str(raw.get("type") or "").lower()
        if typ in MIME_BY_EXT:
            path_ext = typ
        elif level in HIGH_AUDIO_LEVELS or br >= 900000:
            path_ext = "flac"
        else:
            path_ext = "mp3"
    ext = path_ext
    # drop stale audio files (e.g. re-cache mp3 → flac)
    for old in config.CACHE_DIR.glob(f"{sid}.audio.*"):
        try:
            if old.is_file():
                old.unlink()
        except Exception:
            pass
    audio_path = config.CACHE_DIR / f"{sid}.audio.{ext}"
    tmp_path = config.CACHE_DIR / f"{sid}.audio.part"

    log.info("caching song %s level=%s -> %s", sid, level, audio_path.name)
    with SESSION.get(audio_url, stream=True, timeout=120) as resp:
        resp.raise_for_status()
        with open(tmp_path, "wb") as f:
            for chunk in resp.iter_content(256 * 1024):
                if chunk:
                    f.write(chunk)
    tmp_path.replace(audio_path)

    cover_url = try_https(raw.get("picUrl") or "")
    cover_path = config.CACHE_DIR / f"{sid}.cover.jpg"
    if cover_url and cover_host_ok(cover_url):
        try:
            cr = SESSION.get(cover_url, timeout=20)
            if cr.status_code == 200 and cr.content:
                cover_path.write_bytes(cr.content)
        except Exception as e:
            log.warning("cover cache failed for %s: %s", sid, e)

    try:
        d = upstream.get_lyric(sid)
        lrc_text = make_lyric_text(d, raw.get("name", ""), raw.get("artist", ""))
        if lrc_text.strip():
            (config.CACHE_DIR / f"{sid}.lrc").write_text(lrc_text, encoding="utf-8")
    except Exception:
        pass

    meta = {
        "id": sid,
        "name": raw.get("name", ""),
        "artist": raw.get("artist", ""),
        "album": "",
        "cover": cover_url,
        "duration": 0,
        "br": raw.get("br", 0),
        "size": raw.get("size") or audio_path.stat().st_size,
        "level": raw.get("level") or level,
        "ext": ext,
        "cachedAt": time.time(),
        "audioFile": audio_path.name,
    }
    cache_meta_path(sid).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return build_song_payload(sid, {**raw, **meta}, cached=True)


def list_library() -> list[dict]:
    items = []
    for meta_path in sorted(config.CACHE_DIR.glob("*.meta.json")):
        sid = meta_path.name.replace(".meta.json", "")
        if not find_cached_audio(sid):
            continue
        meta = load_cache_meta(sid) or {"id": sid}
        items.append(build_song_payload(sid, meta, cached=True))
    items.sort(key=lambda x: (x.get("name") or "").lower())
    return items


def delete_cache(sid: str) -> bool:
    removed = False
    for p in config.CACHE_DIR.glob(f"{sid}.*"):
        try:
            p.unlink()
            removed = True
        except Exception:
            pass
    return removed


def stream_local_file(path: Path, range_header: str | None = None):
    name = path.name
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else "mp3"
    content_type = MIME_BY_EXT.get(ext, "audio/mpeg")
    total_size = path.stat().st_size
    start = 0
    end = total_size - 1
    status = 200
    if range_header:
        m = re.match(r"bytes=(\d+)-(\d*)", range_header)
        if m:
            start = int(m.group(1))
            end = int(m.group(2)) if m.group(2) else total_size - 1
            end = min(end, total_size - 1)
            status = 206

    def generate():
        with open(path, "rb") as f:
            f.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                chunk = f.read(min(256 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(end - start + 1),
        "Cache-Control": "public, max-age=3600",
        "Content-Disposition": "inline",
        "X-Kazam-Cache": "hit",
    }
    if status == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{total_size}"
    return generate(), content_type, status, headers


# ── Pages ─────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template(
        "index.html",
        public_base=config.PUBLIC_BASE_URL,
        site_name="KAZAM",
    )


@app.route("/embed")
def embed_page():
    return render_template(
        "embed.html",
        public_base=config.PUBLIC_BASE_URL,
        song_id=request.args.get("id", ""),
        autoplay=request.args.get("autoplay", "0"),
    )


@app.route("/health")
def health():
    import runtime_settings

    lib = list_library()
    return jsonify(
        {
            "ok": True,
            "service": "kazam-vps",
            "public_base": config.PUBLIC_BASE_URL,
            "cache_dir": str(config.CACHE_DIR),
            "cached_songs": len(lib),
            "upstream": runtime_settings.get_upstream(),
        }
    )


@app.route("/favicon.ico")
def favicon():
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
        '<rect width="100" height="100" rx="20" fill="#facc15"/>'
        '<text x="50" y="65" text-anchor="middle" font-size="50" '
        'font-weight="bold" fill="#1a1a2e">♪</text></svg>'
    )
    return Response(svg, mimetype="image/svg+xml")


# ── API ───────────────────────────────────────────────────────────
@app.route("/api/config")
def api_config():
    import runtime_settings

    return jsonify(
        {
            "ok": True,
            "publicBase": config.PUBLIC_BASE_URL,
            "audioLevel": parse_audio_level(config.AUDIO_LEVEL),
            "audioLevels": list(AUDIO_LEVELS),
            "upstream": runtime_settings.get_upstream(),
            "features": {
                "parse": True,
                "localCache": True,
                "library": True,
                "localStoragePlaylist": True,
                "adminGate": True,
                "todayRadio": True,
                "adminUpstream": True,
                "audioLevelSelect": True,
            },
        }
    )


@app.route("/api/parse")
def parse_endpoint():
    """
    Feature 2: one-click parse ID/link → title, artist, cover, stable URLs.
    Query: ?q=  (id or netease URL)
    """
    q = request.args.get("q") or request.args.get("id") or ""
    sid = parse_song_id(q)
    if not sid:
        return jsonify({"ok": False, "error": "cannot parse song id"}), 400
    raw = get_song_meta(sid)
    # Even if url missing, still return meta if we have cache
    if not raw and not is_cached(sid):
        return jsonify({"ok": False, "error": "song not found", "id": sid}), 404
    payload = build_song_payload(sid, raw or load_cache_meta(sid) or {"id": sid})
    # Prefer richer names from raw
    if raw:
        payload["name"] = raw.get("name") or payload["name"]
        payload["artist"] = raw.get("artist") or payload["artist"]
        payload["cover"] = try_https(raw.get("picUrl") or "") or payload["cover"]
        payload["url"] = raw.get("url") or ""
        payload["cdnUrl"] = raw.get("url") or ""
        payload["br"] = raw.get("br") or payload["br"]
        payload["level"] = raw.get("level") or payload["level"]
        payload["size"] = raw.get("size") or payload["size"]
    return jsonify({"ok": True, "data": payload})


@app.route("/api/search")
def search():
    kw = request.args.get("q", "").strip()
    if not kw:
        return jsonify({"ok": False, "data": []})
    # If user pasted a link/id into search, parse directly
    sid = parse_song_id(kw)
    if sid and (re.fullmatch(r"\d{5,}", kw.strip()) or "163.com" in kw or "music.163" in kw):
        raw = get_song_meta(sid)
        if raw:
            return jsonify({"ok": True, "data": [build_song_payload(sid, raw)]})
    try:
        raw_songs = upstream.search_songs(kw, limit=30)
        songs = []
        for s in raw_songs:
            if not s.get("id"):
                continue
            n = normalize_song(s)
            if isinstance(s.get("artist"), str) and s["artist"]:
                n["artist"] = s["artist"]
            if s.get("picUrl") and not n.get("cover"):
                n["cover"] = try_https(s["picUrl"])
            songs.append(n)
        return jsonify({"ok": True, "data": songs})
    except Exception as e:
        log.exception("search failed")
        return jsonify({"ok": False, "error": str(e)}), 500


# ── 今日电台 ─────────────────────────────────────────────────────
# 每次请求重新随机抽歌（可 exclude 上一批，尽量不重复）
RADIO_KEYWORDS = [
    "流行",
    "民谣",
    "摇滚",
    "电子",
    "轻音乐",
    "华语",
    "日语",
    "韩语",
    "R&B",
    "说唱",
    "古风",
    "独立",
    "治愈",
    "夜色",
    "开车",
    "学习",
    "经典老歌",
    "热歌",
    "情歌",
    "粤语",
    "钢琴",
    "爵士",
    "动漫",
    "影视金曲",
    "抖音热歌",
    "欧美流行",
    "校园",
    "抒情",
    "欢快",
    "安静",
    "失眠",
    "雨天",
    "夏天",
    "冬天",
    "旅行",
    "运动",
    "咖啡馆",
    "吉他",
    "乐队",
    "女声",
    "男声",
    "翻唱",
    "原声",
    "BGM",
]


def _today_cn() -> str:
    tz = timezone(timedelta(hours=8))
    return datetime.now(tz).strftime("%Y-%m-%d")


def _search_songs_for_radio(keyword: str, limit: int = 20) -> list[dict]:
    try:
        raw = upstream.search_songs(keyword, limit=limit)
        return [normalize_song(s) for s in raw if s.get("id")]
    except Exception as e:
        log.warning("radio search %s failed: %s", keyword, e)
        return []


def build_today_radio(
    limit: int = 30,
    exclude_ids: set[str] | None = None,
) -> dict:
    """每次真实随机生成一批歌；exclude_ids 尽量避开上一批。"""
    today = _today_cn()
    limit = max(5, min(50, int(limit or 30)))
    exclude_ids = {str(x) for x in (exclude_ids or set()) if str(x).strip()}
    rng = random.SystemRandom()

    kws = list(RADIO_KEYWORDS)
    rng.shuffle(kws)
    # 多抽一些关键词，池子更大，刷新时差异更明显
    pick_n = min(12, len(kws))
    chosen = kws[:pick_n]

    pool: list[dict] = []
    seen: set[str] = set(exclude_ids)

    # 服务端缓存曲库随机混入一部分
    try:
        lib = list(list_library())
        rng.shuffle(lib)
        for item in lib[: max(5, limit // 3)]:
            sid = str(item.get("id") or "")
            if sid and sid not in seen:
                seen.add(sid)
                pool.append(item)
    except Exception:
        pass

    for kw in chosen:
        batch = _search_songs_for_radio(kw, limit=20)
        rng.shuffle(batch)
        for s in batch:
            sid = str(s.get("id") or "")
            if not sid or sid in seen:
                continue
            seen.add(sid)
            pool.append(s)
        if len(pool) >= limit * 4:
            break

    if len(pool) < limit:
        # 兜底：不带 exclude 再搜热门（仍尽量避开）
        for kw in ("热歌", "流行", "华语"):
            for s in _search_songs_for_radio(kw, limit=30):
                sid = str(s.get("id") or "")
                if not sid or sid in {str(x.get("id")) for x in pool}:
                    continue
                if sid in exclude_ids and len(pool) >= limit:
                    continue
                pool.append(s)
            if len(pool) >= limit:
                break

    # 优先选不在 exclude 里的
    fresh = [s for s in pool if str(s.get("id")) not in exclude_ids]
    reuse = [s for s in pool if str(s.get("id")) in exclude_ids]
    rng.shuffle(fresh)
    rng.shuffle(reuse)
    ordered = fresh + reuse
    songs = ordered[:limit]
    overlap = sum(1 for s in songs if str(s.get("id")) in exclude_ids)

    return {
        "date": today,
        "songs": songs,
        "keywords": chosen,
        "excluded": len(exclude_ids),
        "overlap": overlap,
    }


@app.route("/api/radio/today")
def radio_today():
    """今日电台：每次请求重新随机一批（可 exclude 上一批 id）。"""
    try:
        limit = int(request.args.get("limit") or 30)
    except ValueError:
        limit = 30
    raw_ex = request.args.get("exclude") or ""
    exclude_ids = {x.strip() for x in raw_ex.split(",") if x.strip()}
    # 限制 exclude 长度，防超长 query
    if len(exclude_ids) > 80:
        exclude_ids = set(list(exclude_ids)[:80])
    try:
        result = build_today_radio(limit=limit, exclude_ids=exclude_ids)
        return jsonify(
            {
                "ok": True,
                "date": result["date"],
                "count": len(result["songs"]),
                "data": result["songs"],
                "keywords": result.get("keywords") or [],
                "overlap": result.get("overlap", 0),
            }
        )
    except Exception as e:
        log.exception("radio today failed")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/song/<sid>")
def song(sid: str):
    try:
        try:
            dur = int(request.args.get("duration") or 0)
        except ValueError:
            dur = 0
        level = parse_audio_level(request.args.get("level"))
        raw = get_song_meta(sid, level=level, duration_ms=dur)
        if not raw and not is_cached(sid):
            return jsonify({"ok": False}), 404
        data = build_song_payload(sid, raw or load_cache_meta(sid) or {"id": sid})
        if raw:
            data["url"] = raw.get("url", "")
            data["cdnUrl"] = raw.get("url", "")
            data["br"] = raw.get("br") or data.get("br") or 0
            data["size"] = raw.get("size") or data.get("size") or 0
            data["level"] = raw.get("level") or level or data.get("level") or ""
            data["requestedLevel"] = level
            data["source"] = raw.get("_source") or data.get("source") or ""
            if raw.get("type"):
                data["type"] = raw.get("type")
        else:
            data["requestedLevel"] = level
        return jsonify({"ok": True, "data": data})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/resolve/<sid>")
def resolve(sid: str):
    """Stable stream URL + meta for personal-site radio."""
    level = parse_audio_level(request.args.get("level"))
    raw = get_song_meta(sid, level=level)
    if not raw and not is_cached(sid):
        return jsonify({"ok": False, "error": "unavailable"}), 404
    data = build_song_payload(sid, raw or load_cache_meta(sid) or {"id": sid})
    data["requestedLevel"] = level
    return jsonify({"ok": True, "data": data})


@app.route("/api/lyric/<sid>")
def lyric(sid: str):
    # Prefer local lrc if cached
    lrc_path = config.CACHE_DIR / f"{sid}.lrc"
    if lrc_path.exists():
        text = lrc_path.read_text(encoding="utf-8", errors="ignore")
        return jsonify(
            {
                "ok": True,
                "data": {
                    "lrc": text,
                    "tlrc": "",
                    "romalrc": "",
                    "klyric": "",
                    "cached": True,
                },
            }
        )
    d = upstream.get_lyric(sid)
    return jsonify(
        {
            "ok": True,
            "data": {
                "lrc": d.get("lrc", ""),
                "tlrc": d.get("tlyric", ""),
                "romalrc": d.get("romalrc", ""),
                "klyric": d.get("klyric", ""),
                "cached": False,
            },
        }
    )


@app.route("/api/playlist/<pid>")
def playlist(pid: str):
    try:
        tracks = upstream.get_playlist_tracks(pid)
        songs = []
        for t in tracks:
            if not t.get("id"):
                continue
            n = normalize_song(t)
            if isinstance(t.get("artist"), str) and t["artist"]:
                n["artist"] = t["artist"]
            if t.get("picUrl") and not n.get("cover"):
                n["cover"] = try_https(t["picUrl"])
            songs.append(n)
        return jsonify({"ok": True, "data": songs})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/cover-proxy")
def cover_proxy():
    url = request.args.get("url", "").strip()
    if not url or not url.startswith(("http://", "https://")):
        return "", 400
    if not cover_host_ok(url):
        return jsonify({"ok": False, "error": "host not allowed"}), 403
    try:
        r = SESSION.get(url, timeout=12)
        if r.status_code != 200:
            return "", 404
        ct = r.headers.get("content-type", "image/jpeg")
        resp = Response(r.content, mimetype=ct)
        resp.headers["Cache-Control"] = "public, max-age=86400"
        return resp
    except Exception:
        return "", 404


@app.route("/api/stream/<sid>")
def stream(sid: str):
    """Prefer local cache for low/mid quality; high quality always live."""
    level = parse_audio_level(request.args.get("level"))
    range_header = request.headers.get("Range")
    # Local cache is typically 320k — skip it when user wants FLAC / 母带
    use_cache = level not in HIGH_AUDIO_LEVELS
    if use_cache:
        local = find_cached_audio(sid)
        if local:
            gen, ct, status, headers = stream_local_file(local, range_header)
            headers = dict(headers or {})
            headers["X-Kazam-Level"] = level
            headers["X-Kazam-Cache"] = "hit"
            return Response(gen, status=status, mimetype=ct, headers=headers)

    audio_url = get_audio_url(sid, level=level)
    if not audio_url:
        return jsonify({"ok": False, "error": "no url", "level": level}), 404

    gen, ct, _ext, total_size = stream_audio(audio_url, range_header)
    headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "no-cache",
        "Content-Disposition": "inline",
        "X-Kazam-Cache": "miss",
        "X-Kazam-Level": level,
    }

    if range_header and total_size > 0:
        m = re.match(r"bytes=(\d+)-(\d*)", range_header)
        if m:
            start = int(m.group(1))
            end = int(m.group(2)) if m.group(2) else total_size - 1
            end = min(end, total_size - 1)
            headers["Content-Range"] = f"bytes {start}-{end}/{total_size}"
            headers["Content-Length"] = str(end - start + 1)
            gen, ct, _, _ = stream_audio(audio_url, f"bytes={start}-{end}")
            return Response(gen, status=206, mimetype=ct, headers=headers)

    if total_size > 0:
        headers["Content-Length"] = str(total_size)
    return Response(gen, status=200, mimetype=ct, headers=headers)


@app.route("/api/download/<sid>")
def download(sid: str):
    """
    Download at requested quality via ?level=.
    Always resolve live URL so 臻品母带 / FLAC 等可自选；
    仅在上游失败时回退本地缓存。?cache=1 可强制本地文件。
    """
    level = parse_audio_level(request.args.get("level"))
    force_cache = (request.args.get("cache") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )

    def _serve_local(local: Path, meta: dict, cache_tag: str):
        name = meta.get("name", sid)
        artist = meta.get("artist", "")
        title = f"{artist} - {name}" if artist else name
        ext = local.name.rsplit(".", 1)[-1]
        ct = MIME_BY_EXT.get(ext, "audio/mpeg")
        hdrs = make_download_headers(f"{title}.{ext}", fallback=f"{name}.{ext}")
        hdrs["Content-Type"] = ct
        hdrs["Content-Length"] = str(local.stat().st_size)
        hdrs["X-Kazam-Level"] = level
        hdrs["X-Kazam-Cache"] = cache_tag
        return Response(local.read_bytes(), status=200, headers=hdrs)

    if force_cache:
        local = find_cached_audio(sid)
        if local:
            return _serve_local(local, load_cache_meta(sid) or {}, "hit-forced")

    raw = get_song_meta(sid, level=level) or {}
    audio_url = raw.get("url", "")
    if not audio_url:
        local = find_cached_audio(sid)
        if local:
            return _serve_local(local, load_cache_meta(sid) or raw, "hit-fallback")
        return jsonify({"ok": False, "error": "no url", "level": level}), 404

    name = raw.get("name", sid)
    artist = raw.get("artist", "")
    title = f"{artist} - {name}" if artist else name
    ext = (
        urlparse(audio_url).path.rsplit(".", 1)[-1].lower()
        if "." in urlparse(audio_url).path
        else ""
    )
    if ext not in MIME_BY_EXT:
        br = int(raw.get("br") or 0)
        typ = str(raw.get("type") or "").lower()
        if typ in MIME_BY_EXT:
            ext = typ
        elif level in HIGH_AUDIO_LEVELS or br >= 900000:
            ext = "flac"
        else:
            ext = "mp3"
    filename = f"{title}.{ext}"
    gen, ct, _, total_size = stream_audio(audio_url)
    hdrs = make_download_headers(filename, fallback=f"{name}.{ext}")
    hdrs["Content-Type"] = ct
    hdrs["X-Kazam-Level"] = level
    hdrs["X-Kazam-Cache"] = "miss"
    if raw.get("br"):
        hdrs["X-Kazam-Br"] = str(raw.get("br"))
    if total_size > 0:
        hdrs["Content-Length"] = str(total_size)
    return Response(gen, status=200, headers=hdrs)


@app.route("/api/download-lyric/<sid>")
def download_lyric(sid: str):
    lrc_path = config.CACHE_DIR / f"{sid}.lrc"
    if lrc_path.exists():
        content = lrc_path.read_text(encoding="utf-8", errors="ignore")
        meta = load_cache_meta(sid) or {}
        name = meta.get("name", sid)
        artist = meta.get("artist", "")
    else:
        d = upstream.get_lyric(sid)
        raw = get_song_meta(sid)
        name = raw.get("name", sid)
        artist = raw.get("artist", "")
        content = make_lyric_text(d, name, artist)
    title = f"{artist} - {name}" if artist else name
    hdrs = make_download_headers(f"{title}.lrc", fallback=f"{name}.lrc")
    hdrs["Content-Type"] = "text/plain; charset=utf-8"
    return Response(content, status=200, headers=hdrs)


# ── Feature 4: local cache library ────────────────────────────────
@app.route("/api/cache/<sid>", methods=["POST"])
def cache_create(sid: str):
    """Cache song audio to server disk — admin only. Honors ?level=."""
    denied = require_admin()
    if denied:
        return denied
    try:
        level = parse_audio_level(
            request.args.get("level")
            or (request.get_json(silent=True) or {}).get("level")
        )
        data = cache_song(sid, level=level)
        return jsonify({"ok": True, "data": data})
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        log.exception("cache failed for %s", sid)
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/cache/<sid>", methods=["DELETE"])
def cache_delete(sid: str):
    denied = require_admin()
    if denied:
        return denied
    ok = delete_cache(sid)
    return jsonify({"ok": ok})


@app.route("/api/cache/<sid>/status")
def cache_status(sid: str):
    hit = is_cached(sid)
    meta = load_cache_meta(sid)
    audio = find_cached_audio(sid)
    return jsonify(
        {
            "ok": True,
            "cached": hit,
            "size": audio.stat().st_size if audio else 0,
            "meta": meta,
        }
    )


@app.route("/api/cache/<sid>/cover")
def cache_cover(sid: str):
    path = config.CACHE_DIR / f"{sid}.cover.jpg"
    if not path.exists():
        return "", 404
    return Response(
        path.read_bytes(),
        mimetype="image/jpeg",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.route("/api/library")
def library():
    """List server-side cached songs."""
    return jsonify({"ok": True, "data": list_library()})


@app.route("/static/embed.js")
def embed_js_route():
    return send_from_directory(app.static_folder, "embed.js")


def create_app():
    return app


if __name__ == "__main__":
    log.info("KAZAM VPS starting on %s:%s", config.HOST, config.PORT)
    log.info("Public base: %s", config.PUBLIC_BASE_URL)
    log.info("Cache dir: %s", config.CACHE_DIR)
    app.run(host=config.HOST, port=config.PORT, debug=False, threaded=True)
