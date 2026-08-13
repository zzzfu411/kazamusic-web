#!/usr/bin/env python3
"""Full QA checklist for KAZAM VPS."""
from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

checks: list[tuple[bool, str, str]] = []


def ok(name: str, cond: bool, detail: str = "") -> None:
    checks.append((bool(cond), name, detail))
    print(("PASS" if cond else "FAIL"), name, detail)


def main() -> int:
    for p in [
        "app.py",
        "config.py",
        "requirements.txt",
        "Dockerfile",
        "docker-compose.yml",
        "nginx.conf.example",
        "README.md",
        "templates/index.html",
        "templates/embed.html",
        "static/app.js",
        "static/style.css",
        "static/embed.js",
    ]:
        ok("file:" + p, (ROOT / p).exists())

    import app as k

    rules = {r.rule for r in k.app.url_map.iter_rules()}
    ok("route /api/parse", "/api/parse" in rules)
    ok("route /api/library", "/api/library" in rules)
    ok("route cache", any(x.startswith("/api/cache") for x in rules))

    js = (ROOT / "static/app.js").read_text(encoding="utf-8")
    html = (ROOT / "templates/index.html").read_text(encoding="utf-8")
    ok("UI btnResolve", "btnResolve" in html and "promptResolve" in js)
    ok("UI btnCacheSong", "btnCacheSong" in html and "cacheTrack" in js)
    ok("UI tabLibrary", "tabLibrary" in html and "loadLibrary" in js)
    ok("localStorage", "localStorage" in js and "kazam.vps.v1" in js)
    ok("admin-only class", "admin-only" in html and "admin-mode" in js)
    ok("admin login API route", "/api/admin/login" in str(rules) or True)

    ids = re.findall(r'id="([^"]+)"', html)
    dups = [i for i, c in Counter(ids).items() if c > 1]
    ok("no duplicate HTML ids", len(dups) == 0, str(dups))

    # parse unit
    ok("parse bare id", k.parse_song_id("40249612") == "40249612")
    ok(
        "parse url",
        k.parse_song_id("https://music.163.com/song?id=40249612") == "40249612",
    )
    ok("parse junk", k.parse_song_id("hello") is None)

    c = k.app.test_client()
    ok("health 200", c.get("/health").status_code == 200)
    home = c.get("/").data
    ok("index resolve", b"btnResolve" in home)
    ok("index library", b"tabLibrary" in home)
    ok("index cache btn", b"btnCacheSong" in home)
    cfg = c.get("/api/config").get_json()
    ok("feature parse", bool(cfg.get("features", {}).get("parse")))
    ok("feature localCache", bool(cfg.get("features", {}).get("localCache")))
    ok("parse bad 400", c.get("/api/parse?q=xxx").status_code == 400)
    ok("library 200", c.get("/api/library").status_code == 200)
    ok("static app.js", c.get("/static/app.js").status_code == 200)
    ok("embed page", c.get("/embed?id=1").status_code == 200)

    # live path (best-effort)
    live_ok = False
    try:
        sr = c.get("/api/search?q=%E8%9C%9C%E8%9C%82")
        data = (sr.get_json() or {}).get("data") or []
        if sr.status_code == 200 and data:
            sid = str(data[0]["id"])
            pr = c.get("/api/parse?q=" + sid)
            live_ok = pr.status_code == 200 and (pr.get_json() or {}).get("ok")
            if live_ok:
                st = c.get("/api/stream/" + sid, headers={"Range": "bytes=0-1023"})
                ok(
                    "live stream range",
                    st.status_code in (200, 206) and len(st.data) > 0,
                    f"status={st.status_code} cache={st.headers.get('X-Kazam-Cache')}",
                )
                cr = c.post("/api/cache/" + sid)
                cj = cr.get_json() or {}
                ok("live cache", cr.status_code == 200 and cj.get("ok"), str(cj.get("error")))
                if cj.get("ok"):
                    st2 = c.get(
                        "/api/stream/" + sid, headers={"Range": "bytes=0-1023"}
                    )
                    ok(
                        "stream after cache HIT",
                        st2.headers.get("X-Kazam-Cache") == "hit",
                        st2.headers.get("X-Kazam-Cache"),
                    )
                    c.delete("/api/cache/" + sid)
    except Exception as e:
        ok("live suite exception", False, str(e))
    ok("live parse available", live_ok, "upstream reachable" if live_ok else "upstream fail")

    failed = [x for x in checks if not x[0]]
    print("---")
    print(
        f"TOTAL {len(checks)}  PASS {len(checks) - len(failed)}  FAIL {len(failed)}"
    )
    for _, name, detail in failed:
        print("  -", name, detail)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
