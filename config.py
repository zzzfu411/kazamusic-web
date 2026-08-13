"""Runtime configuration for KAZAM VPS."""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def _load_dotenv() -> None:
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        os.environ.setdefault(key, val)


_load_dotenv()


def env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def env_bool(key: str, default: bool = False) -> bool:
    raw = os.environ.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_list(key: str, default: str = "") -> list[str]:
    raw = os.environ.get(key, default)
    return [x.strip() for x in raw.split(",") if x.strip()]


HOST = env("HOST", "0.0.0.0")
PORT = int(env("PORT", "8080"))
PUBLIC_BASE_URL = env("PUBLIC_BASE_URL", "http://127.0.0.1:8080").rstrip("/")
CORS_ORIGINS = env_list(
    "CORS_ORIGINS",
    "http://127.0.0.1:8080",
)
# No built-in third-party endpoints. Set in .env or add sources in admin UI.
UPSTREAM_API = env("UPSTREAM_API", "").rstrip("/")
METING_APIS = env("METING_APIS", "")
AUDIO_LEVEL = env("AUDIO_LEVEL", "exhigh")
LOG_LEVEL = env("LOG_LEVEL", "INFO")
TRUST_PROXY = env_bool("TRUST_PROXY", True)

# Local song cache (audio + meta + cover) — survives CDN link expiry
CACHE_DIR = Path(env("CACHE_DIR", str(BASE_DIR / "data" / "cache")))
CACHE_DIR.mkdir(parents=True, exist_ok=True)
# Max concurrent cache size soft limit (0 = unlimited)
CACHE_MAX_SONGS = int(env("CACHE_MAX_SONGS", "0"))

# Admin gate (copy stream URL / server cache). MUST set in production .env
ADMIN_PASSWORD = env("ADMIN_PASSWORD", "change-me")
ADMIN_TOKEN_TTL = int(env("ADMIN_TOKEN_TTL", "86400"))  # seconds
ADMIN_SECRET = env("ADMIN_SECRET", "change-me-to-a-long-random-string")

# Cover proxy allowlist (host suffix match)
COVER_HOST_SUFFIXES = (
    "music.126.net",
    "126.net",
    "netease.com",
    "ydstatic.com",
)
