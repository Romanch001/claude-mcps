#!/usr/bin/env python3
"""
create_render_services.py
Creates (or redeploys) all 20 MCP services on Render via the API.

Usage:
  python3 create_render_services.py           # create missing + redeploy failed
  python3 create_render_services.py --redeploy # force redeploy all existing services

Requires:
  pip install httpx
"""

import sys
import time
import httpx

API_KEY   = "rnd_CvrOOy35aCkeOPhro46TiV8kkkhE"
REPO_URL  = "https://github.com/Romanch001/claude-mcps"
BRANCH    = "master"
BASE      = "https://api.render.com/v1"
HEADERS   = {"Authorization": f"Bearer {API_KEY}", "Accept": "application/json",
             "Content-Type": "application/json"}

# ---------------------------------------------------------------------------
# Service definitions
# ---------------------------------------------------------------------------
SERVICES = [
    {"name": "blender-mcp",        "dir": "blender-mcp",        "envVars": []},
    {"name": "freecad-mcp",        "dir": "freecad-mcp",         "envVars": []},
    {"name": "openscad-mcp",       "dir": "openscad-mcp",        "envVars": []},
    {"name": "arduino-mcp",        "dir": "arduino-mcp",         "envVars": []},
    {"name": "home-assistant-mcp", "dir": "home-assistant-mcp",  "envVars": [
        {"key": "HA_URL",   "value": ""},
        {"key": "HA_TOKEN", "value": ""},
    ]},
    {"name": "spotify-mcp",        "dir": "spotify-mcp",         "envVars": [
        {"key": "SPOTIFY_CLIENT_ID",     "value": ""},
        {"key": "SPOTIFY_CLIENT_SECRET", "value": ""},
    ]},
    {"name": "reddit-mcp",         "dir": "reddit-mcp",          "envVars": []},
    {"name": "discord-mcp",        "dir": "discord-mcp",         "envVars": [
        {"key": "DISCORD_WEBHOOK_URL", "value": ""},
    ]},
    {"name": "wikipedia-mcp",      "dir": "wikipedia-mcp",       "envVars": []},
    {"name": "notion-mcp",         "dir": "notion-mcp",          "envVars": [
        {"key": "NOTION_API_KEY", "value": ""},
    ]},
    {"name": "google-maps-mcp",    "dir": "google-maps-mcp",     "envVars": [
        {"key": "GOOGLE_MAPS_API_KEY", "value": ""},
    ]},
    {"name": "google-sheets-mcp",  "dir": "google-sheets-mcp",   "envVars": [
        {"key": "GOOGLE_SERVICE_ACCOUNT_JSON", "value": ""},
    ]},
    {"name": "google-translate-mcp","dir": "google-translate-mcp","envVars": [
        {"key": "GOOGLE_TRANSLATE_API_KEY", "value": ""},
    ]},
    {"name": "google-photos-mcp",  "dir": "google-photos-mcp",   "envVars": [
        {"key": "GOOGLE_PHOTOS_ACCESS_TOKEN", "value": ""},
    ]},
    {"name": "strava-mcp",         "dir": "strava-mcp",          "envVars": [
        {"key": "STRAVA_CLIENT_ID",      "value": ""},
        {"key": "STRAVA_CLIENT_SECRET",  "value": ""},
        {"key": "STRAVA_REFRESH_TOKEN",  "value": ""},
    ]},
    {"name": "myfitnesspal-mcp",   "dir": "myfitnesspal-mcp",    "envVars": []},
    {"name": "apple-health-mcp",   "dir": "apple-health-mcp",    "envVars": []},
    {"name": "gtfs-mcp",           "dir": "gtfs-mcp",            "envVars": [
        {"key": "GTFS_FEED_URL", "value": ""},
    ]},
    {"name": "airbnb-mcp",         "dir": "airbnb-mcp",          "envVars": []},
    {"name": "nptel-mcp",          "dir": "nptel-mcp",           "envVars": []},
]

# ---------------------------------------------------------------------------

def get_owner_id(client: httpx.Client) -> str:
    r = client.get(f"{BASE}/owners?limit=10")
    r.raise_for_status()
    owners = r.json()
    if not owners:
        sys.exit("ERROR: No owners found for this API key.")
    owner = owners[0]["owner"]
    print(f"✓ Account: {owner['name']}  (id={owner['id']})")
    return owner["id"]


def list_existing_services(client: httpx.Client, owner_id: str) -> dict:
    """Return dict of name -> service object for all existing services."""
    existing = {}
    cursor = None
    while True:
        params = {"ownerId": owner_id, "limit": 100}
        if cursor:
            params["cursor"] = cursor
        r = client.get(f"{BASE}/services", params=params)
        r.raise_for_status()
        data = r.json()
        for item in data:
            svc = item["service"]
            existing[svc["name"]] = svc
        if len(data) < 100:
            break
        cursor = data[-1]["cursor"]
    return existing


def create_service(client: httpx.Client, owner_id: str, svc: dict) -> dict:
    payload = {
        "type": "web_service",
        "name": svc["name"],
        "ownerId": owner_id,
        "repo": REPO_URL,
        "branch": BRANCH,
        "autoDeploy": "yes",
        "serviceDetails": {
            "runtime": "docker",
            "dockerfilePath": f"./{svc['dir']}/Dockerfile",
            "dockerContext": f"./{svc['dir']}",
            "healthCheckPath": "/health",
            "plan": "free",
        },
    }
    if svc["envVars"]:
        payload["envVars"] = svc["envVars"]

    r = client.post(f"{BASE}/services", json=payload)
    if r.status_code in (200, 201):
        return r.json()
    return {"error": r.status_code, "detail": r.text[:400]}


def redeploy_service(client: httpx.Client, service_id: str, service_name: str) -> bool:
    """Trigger a manual redeploy for an existing service."""
    r = client.post(f"{BASE}/services/{service_id}/deploys", json={"clearCache": "clear"})
    return r.status_code in (200, 201)


def main():
    force_redeploy = "--redeploy" in sys.argv

    print("=" * 62)
    print("  Render MCP Services — Auto-Creator / Redeployer")
    print("=" * 62)
    print(f"  Branch: {BRANCH}")
    print(f"  Repo:   {REPO_URL}")
    print()

    with httpx.Client(headers=HEADERS, timeout=30) as client:
        owner_id = get_owner_id(client)
        existing = list_existing_services(client, owner_id)
        our_names = {s["name"] for s in SERVICES}
        our_existing = {k: v for k, v in existing.items() if k in our_names}
        print(f"  MCP services already on Render: {len(our_existing)}/20")
        print()

        created, redeployed, skipped, failed = [], [], [], []

        for i, svc in enumerate(SERVICES, 1):
            name = svc["name"]

            if name in our_existing:
                if force_redeploy:
                    sid = our_existing[name]["id"]
                    ok = redeploy_service(client, sid, name)
                    status = "REDEPLOY ✓" if ok else "REDEPLOY ✗"
                    print(f"[{i:02d}/20] {status:12s} {name}")
                    (redeployed if ok else failed).append(name)
                else:
                    # Redeploy only if the service is in a failed state
                    svc_status = our_existing[name].get("suspended", "")
                    deploy_status = our_existing[name].get("serviceDetails", {})
                    print(f"[{i:02d}/20] EXISTS       {name}  — triggering redeploy")
                    sid = our_existing[name]["id"]
                    ok = redeploy_service(client, sid, name)
                    (redeployed if ok else skipped).append(name)
                time.sleep(0.3)
                continue

            print(f"[{i:02d}/20] CREATE       {name} ...", end=" ", flush=True)
            result = create_service(client, owner_id, svc)

            if "error" in result:
                print(f"FAILED ({result['error']}): {result['detail'][:80]}")
                failed.append(name)
            else:
                service_id = result.get("service", {}).get("id", "?")
                print(f"OK  (id={service_id})")
                created.append(name)

            time.sleep(0.5)

    print()
    print("=" * 62)
    print(f"  Created: {len(created)}  Redeployed: {len(redeployed)}  "
          f"Skipped: {len(skipped)}  Failed: {len(failed)}")
    print("=" * 62)

    if created:
        print("\n✅ New services created:")
        for n in created:
            print(f"   https://{n}.onrender.com/sse")

    if redeployed:
        print(f"\n🔄 Redeployed ({len(redeployed)} services) — builds running now")

    if failed:
        print("\n❌ Failed:")
        for n in failed:
            print(f"   {n}")

    print("\nMonitor builds: https://dashboard.render.com/")
    print("Each build takes ~3-5 min. /health will return 200 when live.")


if __name__ == "__main__":
    main()
