#!/usr/bin/env python3
"""Delete ALL remaining MCP services from Render."""
import time, httpx, re

API_KEY = "rnd_CvrOOy35aCkeOPhro46TiV8kkkhE"
BASE    = "https://api.render.com/v1"
HDR     = {"Authorization": f"Bearer {API_KEY}", "Accept": "application/json"}

with httpx.Client(headers=HDR, timeout=30) as c:
    owners = c.get(f"{BASE}/owners?limit=10").json()
    oid    = owners[0]["owner"]["id"]
    data   = c.get(f"{BASE}/services?ownerId={oid}&limit=100").json()
    for item in data:
        s = item["service"]
        if "mcp" in s["name"]:
            r = c.delete(f"{BASE}/services/{s['id']}")
            print(f"{'DELETED' if r.status_code in (200,204) else 'FAILED':7s}  {s['name']}")
            time.sleep(0.3)
print("Done.")
