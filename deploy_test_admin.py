#!/usr/bin/env python3
import json
import os
import sys
import urllib.request

base = "http://127.0.0.1:8080"
password = os.environ.get("ADMIN_PASSWORD")
if not password:
    sys.exit("set ADMIN_PASSWORD (the value in .env) before running this check")

def post(path, data=None, headers=None):
    body = None if data is None else json.dumps(data).encode()
    req = urllib.request.Request(
        base + path,
        data=body,
        headers=headers or {"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())

print("no auth cache", post("/api/cache/1", {}))
st, j = post("/api/admin/login", {"password": password})
print("login", st, j)
if j.get("token"):
    st2, j2 = post(
        "/api/cache/1",
        {},
        {"Content-Type": "application/json", "X-Admin-Token": j["token"]},
    )
    print("cache authed", st2, j2)
