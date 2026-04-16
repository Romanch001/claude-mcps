#!/usr/bin/env python3
"""
set_render_env_vars.py — ONE-TIME script, DO NOT COMMIT (it's in .gitignore).
Sets API keys on Render services via the Render API, then triggers a redeploy.

Usage:
    python3 set_render_env_vars.py
"""
import httpx, time

API_KEY = "rnd_CvrOOy35aCkeOPhro46TiV8kkkhE"
BASE    = "https://api.render.com/v1"
HDR     = {"Authorization": f"Bearer {API_KEY}", "Accept": "application/json",
           "Content-Type": "application/json"}

# ── Keys to set ──────────────────────────────────────────────────────────────
ENV_CONFIG = {
    "notion-mcp":        [{"key": "NOTION_API_KEY",  "value": "ntn_o11434844142nXGRz3wZ53QkYfJT2LDnRpj8Ne13o8gaCG"}],
    "wolfram-alpha-mcp": [{"key": "WOLFRAM_APP_ID",  "value": "LVHA7LRA2K"}],
}
# ─────────────────────────────────────────────────────────────────────────────

def get_services(client, owner_id):
    """Return dict: canonical-name → service dict (handles random suffixes)."""
    import re
    result = {}
    cursor = None
    while True:
        params = {"ownerId": owner_id, "limit": 100}
        if cursor:
            params["cursor"] = cursor
        data = client.get(f"{BASE}/services", params=params).json()
        for item in data:
            svc  = item["service"]
            name = svc["name"]
            # Strip 4-char random suffix if present (e.g. google-maps-mcp-kz8m)
            parts = name.rsplit("-", 1)
            canon = parts[0] if len(parts) == 2 and re.match(r'^[a-z0-9]{4}$', parts[1]) else name
            if canon in ENV_CONFIG:
                result[canon] = svc
        if len(data) < 100:
            break
        cursor = data[-1]["cursor"]
    return result

def set_env_vars(client, service_id, env_vars):
    r = client.put(f"{BASE}/services/{service_id}/env-vars", json=env_vars)
    return r.status_code, r.text[:200]

def redeploy(client, service_id):
    r = client.post(f"{BASE}/services/{service_id}/deploys", json={"clearCache": "do_not_clear"})
    return r.status_code

def main():
    with httpx.Client(headers=HDR, timeout=30) as client:
        owners   = client.get(f"{BASE}/owners?limit=10").json()
        owner_id = owners[0]["owner"]["id"]
        print(f"Account: {owners[0]['owner']['name']}\n")

        services = get_services(client, owner_id)

        for canon_name, env_vars in ENV_CONFIG.items():
            if canon_name not in services:
                print(f"[SKIP]  {canon_name} — service not found on Render")
                continue

            svc = services[canon_name]
            sid = svc["id"]
            actual_name = svc["name"]
            print(f"Setting env vars on  {actual_name} ({sid}) ...")

            code, body = set_env_vars(client, sid, env_vars)
            if code in (200, 201):
                print(f"  ✓ env vars updated")
            else:
                print(f"  ✗ failed ({code}): {body}")
                continue

            time.sleep(0.5)

            code2 = redeploy(client, sid)
            if code2 in (200, 201):
                print(f"  ✓ redeploy triggered — will be live in ~3-5 min")
            else:
                print(f"  ✗ redeploy failed ({code2})")

            time.sleep(0.3)

        print("\nDone. You can delete this file now.")

if __name__ == "__main__":
    main()
