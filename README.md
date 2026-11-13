# KAZAM Web

A self-hosted Flask web music player and streaming proxy with multi-upstream audio resolution, server-side disk caching, synchronized lyrics, and an embeddable mini-player.

English · [简体中文](README.zh-CN.md)

## Features

- **Multi-upstream resolution** — Searches and resolves tracks across configurable backends (`GD Studio`, `ChKSz`, `Meting`, `ORS`, `HiFLAC`) with automatic fallback (`GD` → `ChKSz` → `Meting`), single-flight request deduplication, and selectable audio quality (`standard` through `lossless` / `jymaster`).
- **Server-side disk cache** — Admin-triggered caching (`POST /api/cache/{id}`) persists audio, cover art, and lyrics under `data/cache/` so `/api/stream/{id}` survives upstream CDN link expiry, with optional Nginx `X-Accel-Redirect` offload.
- **Ambient player & local queues** — Cover-derived color palette, click-to-seek synchronized lyrics, Media Session lock-screen controls, keyboard shortcuts, and separate `localStorage` playlists with NetEase playlist, JSON, M3U, and plain-text import/export.
- **Stable stream & embed endpoints** — Exposes `/api/stream/{id}` with HTTP `Range` passthrough, `/api/parse` for bare song IDs or NetEase URLs, `/embed?id={id}` iframe widgets, and shareable deep links (`/?play=`, `/?parse=`, `/?q=`).
- **Outbound SSRF guards** — Validates upstream audio redirects and `/api/cover-proxy` requests against host-suffix allowlists, ports `80`/`443`, and public-IP resolution (`safe_fetch.py`).

## Quick Start

Requires **Python 3.12+** (or **Docker**).

```bash
git clone https://github.com/zzzfu411/kazamusic-web.git
cd kazamusic-web
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python app.py
```

Open `http://127.0.0.1:8080` in your browser, or deploy with Docker Compose:

```bash
docker compose up -d --build
```

## Configuration

Copy `.env.example` to `.env` before starting. Click the top-left `KAZAM` wordmark five times in the web UI to unlock admin controls (disk caching, upstream toggles, and quota inspection).

| Variable | Default | Purpose |
| --- | --- | --- |
| `HOST` / `PORT` | `0.0.0.0` / `8080` | Bind address and HTTP listen port |
| `PUBLIC_BASE_URL` | `https://music.yeuxark.com` | Public base URL for share and embed links |
| `CORS_ORIGINS` | `https://yeuxark.com,...` | Comma-separated allowed cross-origin domains |
| `UPSTREAM_API` | `https://api.chksz.com/api` | Primary ChKSz endpoint (paired with `CHKSZ_API_KEY`) |
| `METING_APIS` | `https://api.qijieya.cn/meting/` | Comma-separated Meting fallback endpoints |
| `AUDIO_LEVEL` | `exhigh` | Default quality (`standard`, `higher`, `exhigh`, `lossless`, `hires`, `jymaster`) |
| `CACHE_DIR` | `./data/cache` | Directory for cached audio, lyrics, and WebP covers |
| `KAZAM_CACHE_ACCEL` | *(unset)* | Internal Nginx `X-Accel-Redirect` prefix (e.g. `/_kazam_cache/`) |
| `KAZAM_UPSTREAM_PROXY` | *(unset)* | Optional `CONNECT` relay URL for datacenter-blocked upstreams |
| `ADMIN_PASSWORD` | `change-me` | Password for admin login and server-side cache mutations |

## Verification & Documentation

```bash
python qa_check.py  # route, parser, SSRF guard, and Flask client checks
```

- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) — Production permissions, Nginx `X-Accel-Redirect`, `CONNECT` relay (`deploy/relay/`), HTTP API table, and external site integration
- [nginx.conf.example](nginx.conf.example) & [deploy/](deploy/) — Sample reverse-proxy, systemd (`kazam.service`), and relay unit files

---

For personal learning and self-hosted use only; respect the terms of service and copyright rules of upstream platforms.
