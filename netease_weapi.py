"""
Minimal NetEase Cloud Music weapi client (fallback when third-party proxy is down).
Encryption scheme matches common open-source NeteaseCloudMusicApi implementations.
"""
from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import random
import string
from typing import Any

import requests
from Crypto.Cipher import AES

log = logging.getLogger("kazam.weapi")

MODULUS = (
    "00e0b509f6259df8642dbc35662901477df22677ec152b5ff68ace615bb7"
    "b725152b3ab17a876aea8a5aa76d2e417629ec4ee341f56135fccf695280"
    "104e0312ecbda92557c93870114af6c9d05c4f7f0c3685b7a46bee255932"
    "575cce10b424d813cfe4875d3e82047b97ddef52741d546b8e289dc6935b"
    "3ece0462fe0b3e9e7"
)
NONCE = "0CoJUm6Qyw8W8jud"
PUBKEY = "010001"
IV = b"0102030405060708"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

SESSION = requests.Session()
SESSION.headers.update(
    {
        "User-Agent": UA,
        "Referer": "https://music.163.com/",
        "Origin": "https://music.163.com",
        "Content-Type": "application/x-www-form-urlencoded",
    }
)


def _create_secret_key(size: int = 16) -> str:
    return "".join(random.choice(string.ascii_letters + string.digits) for _ in range(size))


def _aes_encrypt(text: str, key: str) -> str:
    pad = 16 - len(text.encode("utf-8")) % 16
    text_padded = text + chr(pad) * pad
    cipher = AES.new(key.encode("utf-8"), AES.MODE_CBC, IV)
    encrypted = cipher.encrypt(text_padded.encode("utf-8"))
    return base64.b64encode(encrypted).decode("utf-8")


def _rsa_encrypt(text: str) -> str:
    text_rev = text[::-1].encode("utf-8")
    sec = int(binascii.hexlify(text_rev), 16)
    enc = pow(sec, int(PUBKEY, 16), int(MODULUS, 16))
    return format(enc, "x").zfill(256)


def _weapi_payload(data: dict) -> dict[str, str]:
    text = json.dumps(data, separators=(",", ":"))
    sec_key = _create_secret_key(16)
    params = _aes_encrypt(_aes_encrypt(text, NONCE), sec_key)
    enc_sec_key = _rsa_encrypt(sec_key)
    return {"params": params, "encSecKey": enc_sec_key}


def weapi_post(path: str, data: dict, timeout: float = 12) -> dict:
    url = "https://music.163.com/weapi" + path
    r = SESSION.post(url, data=_weapi_payload(data), timeout=timeout)
    r.raise_for_status()
    return r.json()


def search_songs(keyword: str, limit: int = 30) -> list[dict]:
    """Return raw song dicts similar to official search result songs[]."""
    body = weapi_post(
        "/cloudsearch/get/web",
        {
            "s": keyword,
            "type": 1,
            "limit": limit,
            "offset": 0,
            "total": True,
            "csrf_token": "",
        },
    )
    result = body.get("result") or {}
    songs = result.get("songs") or []
    return songs if isinstance(songs, list) else []


def song_urls(ids: list[str], level: str = "exhigh") -> list[dict]:
    """
    Return list of {id, url, br, size, ...}.
    level: standard/higher/exhigh/lossless/hires ...
    """
    # v1 player url
    try:
        body = weapi_post(
            "/song/enhance/player/url/v1",
            {
                "ids": json.dumps([int(i) if str(i).isdigit() else i for i in ids]),
                "level": level,
                "encodeType": "flac" if level in ("lossless", "hires", "jymaster") else "mp3",
                "csrf_token": "",
            },
        )
        data = body.get("data") or []
        if data:
            return data
    except Exception as e:
        log.warning("url/v1 failed: %s", e)

    # legacy
    body = weapi_post(
        "/song/enhance/player/url",
        {
            "ids": json.dumps([int(i) if str(i).isdigit() else i for i in ids]),
            "br": 320000,
            "csrf_token": "",
        },
    )
    return body.get("data") or []


def song_detail(ids: list[str]) -> list[dict]:
    body = weapi_post(
        "/v3/song/detail",
        {
            "c": json.dumps([{"id": int(i)} for i in ids if str(i).isdigit()]),
            "csrf_token": "",
        },
    )
    return body.get("songs") or []


def lyric(sid: str) -> dict:
    body = weapi_post(
        "/song/lyric",
        {
            "id": sid,
            "lv": -1,
            "tv": -1,
            "csrf_token": "",
        },
    )
    return body


def playlist_detail(pid: str) -> dict:
    body = weapi_post(
        "/v3/playlist/detail",
        {"id": pid, "n": 1000, "s": 8, "csrf_token": ""},
    )
    return body


def normalize_from_official_song(s: dict) -> dict:
    """Map official song object → app normalize_song shape fields."""
    artists = s.get("ar") or s.get("artists") or []
    if isinstance(artists, list):
        artist = " / ".join(
            a.get("name", "") for a in artists if isinstance(a, dict)
        )
    else:
        artist = str(artists or "")
    al = s.get("al") or s.get("album") or {}
    album = al.get("name", "") if isinstance(al, dict) else ""
    cover = ""
    if isinstance(al, dict):
        cover = al.get("picUrl") or ""
    return {
        "id": s.get("id"),
        "name": s.get("name", ""),
        "ar": artists,
        "al": al if isinstance(al, dict) else {"name": album, "picUrl": cover},
        "artists": artists,
        "album": al,
        "duration": s.get("dt") or s.get("duration") or 0,
        "picUrl": cover,
    }


def build_play_meta(sid: str, level: str = "exhigh") -> dict:
    """
    Combined meta for /api/song: name/artist/cover/url/br/size/level.
    """
    sid = str(sid)
    detail = {}
    try:
        songs = song_detail([sid])
        if songs:
            detail = songs[0]
    except Exception as e:
        log.warning("song_detail failed: %s", e)

    url_info: dict[str, Any] = {}
    for lv in (level, "exhigh", "higher", "standard"):
        try:
            rows = song_urls([sid], level=lv)
            if rows and rows[0].get("url"):
                url_info = rows[0]
                url_info["_level"] = lv
                break
        except Exception as e:
            log.warning("song_urls level=%s failed: %s", lv, e)

    artists = detail.get("ar") or []
    if isinstance(artists, list):
        artist = " / ".join(
            a.get("name", "") for a in artists if isinstance(a, dict)
        )
    else:
        artist = ""
    al = detail.get("al") or {}
    pic = al.get("picUrl", "") if isinstance(al, dict) else ""

    return {
        "id": int(sid) if sid.isdigit() else sid,
        "name": detail.get("name") or "",
        "artist": artist,
        "picUrl": pic,
        "url": url_info.get("url") or "",
        "br": url_info.get("br") or 0,
        "size": url_info.get("size") or 0,
        "level": url_info.get("_level") or url_info.get("level") or level,
        "md5": url_info.get("md5") or "",
        "type": url_info.get("type") or "",
    }


def available() -> bool:
    """Quick connectivity check to music.163.com."""
    try:
        r = SESSION.get("https://music.163.com", timeout=5)
        return r.status_code < 500
    except Exception:
        return False
