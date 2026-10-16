"""Persist ChKSz daily quota snapshot from response headers."""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import config

log = logging.getLogger("kazam.chksz_quota")

_LOCK = threading.Lock()
_PATH = Path(config.CACHE_DIR).parent / "chksz_quota.json"
_TZ = timezone(timedelta(hours=8))


def _today() -> str:
    return datetime.now(_TZ).strftime("%Y-%m-%d")


def _limit() -> int:
    try:
        n = int(config.env("CHKSZ_DAILY_LIMIT", "500") or "500")
    except ValueError:
        n = 500
    return max(1, n)


def _empty() -> dict[str, Any]:
    return {
        "date": _today(),
        "used": 0,
        "remaining": None,
        "free_remaining": None,
        "paid_remaining": None,
        "limit": _limit(),
        "updated_at": 0,
        "source": "local",
    }


def _read() -> dict[str, Any]:
    if not _PATH.exists():
        return _empty()
    try:
        data = json.loads(_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return _empty()
    except Exception:
        return _empty()
    if str(data.get("date") or "") != _today():
        return _empty()
    data["limit"] = int(data.get("limit") or _limit())
    return data


def _write(data: dict[str, Any]) -> None:
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_PATH)


def snapshot() -> dict[str, Any]:
    with _LOCK:
        data = _read()
    used = int(data.get("used") or 0)
    limit = int(data.get("limit") or _limit())
    free_r = data.get("free_remaining")
    paid_r = data.get("paid_remaining")
    remaining = data.get("remaining")
    if free_r is not None:
        try:
            remaining = int(free_r) + int(paid_r or 0)
            used = max(0, limit - int(free_r))
        except (TypeError, ValueError):
            pass
    elif remaining is None:
        remaining = max(0, limit - used)
    return {
        "ok": True,
        "date": data.get("date") or _today(),
        "used": used,
        "remaining": remaining,
        "freeRemaining": free_r,
        "paidRemaining": paid_r,
        "limit": limit,
        "updatedAt": data.get("updated_at") or 0,
        "source": data.get("source") or "local",
        "hasKey": bool((config.CHKSZ_API_KEY or "").strip()),
    }


def _header(headers: Any, name: str) -> str | None:
    if headers is None:
        return None
    try:
        val = headers.get(name)
        if val is None:
            val = headers.get(name.lower())
        if val is None and hasattr(headers, "items"):
            want = name.lower()
            for k, v in headers.items():
                if str(k).lower() == want:
                    val = v
                    break
        if val is None:
            return None
        return str(val).strip()
    except Exception:
        return None


def _as_int(raw: str | None) -> int | None:
    if raw is None or raw == "":
        return None
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return None


def ingest_response(headers: Any, *, counted: bool = True) -> None:
    """Update snapshot from a ChKSz HTTP response (success or error)."""
    free_r = _as_int(_header(headers, "x-quota-free-remaining"))
    paid_r = _as_int(_header(headers, "x-quota-paid-remaining"))
    with _LOCK:
        data = _read()
        if counted:
            data["used"] = int(data.get("used") or 0) + 1
        if free_r is not None:
            data["free_remaining"] = free_r
            data["paid_remaining"] = paid_r if paid_r is not None else 0
            data["remaining"] = free_r + int(data["paid_remaining"])
            data["used"] = max(0, int(data.get("limit") or _limit()) - free_r)
            data["source"] = "header"
        data["updated_at"] = int(time.time())
        data["limit"] = _limit()
        try:
            _write(data)
        except Exception as e:
            log.debug("quota write failed: %s", e)
