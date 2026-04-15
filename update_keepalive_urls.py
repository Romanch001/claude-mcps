#!/usr/bin/env python3
"""
update_keepalive_urls.py
Fetches all live service URLs from Render API and rewrites
.github/workflows/keepalive.yml with the correct /health endpoints.

Usage:
  python3 update_keepalive_urls.py
"""
import re
import httpx

API_KEY  = "rnd_CvrOOy35aCkeOPhro46TiV8kkkhE"
BASE     = "https://api.render.com/v1"
HEADERS  = {"Authorization": f"Bearer {API_KEY}", "Accept": "application/json"}
WORKFLOW = ".github/workflows/keepalive.yml"

MCP_NAMES = {
    "airbnb-mcp", "apple-health-mcp", "arduino-mcp", "blender-mcp",
    "discord-mcp", "freecad-mcp", "google-maps-mcp", "google-photos-mcp",
    "google-sheets-mcp", "google-translate-mcp", "gtfs-mcp",
    "home-assistant-mcp", "irctc-mcp", "myfitnesspal-mcp", "notion-mcp",
    "nptel-mcp", "openscad-mcp", "reddit-mcp", "spotify-mcp",
    "strava-mcp", "swiggy-mcp", "wikipedia-mcp", "wolfram-alpha-mcp",
    "youtube-mcp", "zomato-mcp",
}

def get_service_urls(client):
    # Get owner
    owners = client.get(f"{BASE}/owners?limit=10").json()
    owner_id = owners[0]["owner"]["id"]

    services = []
    cursor = None
    while True:
        params = {"ownerId": owner_id, "limit": 100}
        if cursor:
            params["cursor"] = cursor
        data = client.get(f"{BASE}/services", params=params).json()
        for item in data:
            svc = item["service"]
            name = svc["name"]
            # Match both exact name and name with random suffix (e.g. google-maps-mcp-kz8m)
            base_name = name
            # Strip random suffix: last segment if it's 4 chars of [a-z0-9]
            parts = name.rsplit("-", 1)
            if len(parts) == 2 and re.match(r'^[a-z0-9]{4}$', parts[1]):
                base_name = parts[0]
            if base_name in MCP_NAMES or name in MCP_NAMES:
                url = svc.get("serviceDetails", {}).get("url", "")
                services.append((base_name, url))
        if len(data) < 100:
            break
        cursor = data[-1]["cursor"]
    return sorted(services, key=lambda x: x[0])

def main():
    print("Fetching service URLs from Render API...")
    with httpx.Client(headers=HEADERS, timeout=30) as client:
        services = get_service_urls(client)

    if not services:
        print("ERROR: No services found. Check your API key.")
        return

    print(f"Found {len(services)} MCP services:")
    health_urls = []
    for name, url in services:
        if url:
            health_url = url.rstrip("/") + "/health"
            health_urls.append((name, health_url))
            print(f"  {name:35s}  {health_url}")
        else:
            print(f"  {name:35s}  (no URL yet — still deploying)")

    # Build the URLS array for the workflow
    url_lines = "\n".join(
        f'            "{u}"' for _, u in health_urls
    )

    workflow_content = f'''name: Keep Render Services Alive

# Ping all MCP health endpoints every 10 minutes so Render free-tier
# services never go to sleep (Render spins down after 15 min inactivity).
on:
  schedule:
    - cron: "*/10 * * * *"
  workflow_dispatch:   # allow manual trigger from GitHub Actions UI

jobs:
  ping:
    runs-on: ubuntu-latest
    steps:
      - name: Ping all MCP health endpoints
        run: |
          URLS=(
{url_lines}
          )
          for url in "${{URLS[@]}}"; do
            code=$(curl -s -o /dev/null -w "%{{http_code}}" --max-time 30 "$url" || echo "ERR")
            echo "$code  $url"
          done
'''

    with open(WORKFLOW, "w") as f:
        f.write(workflow_content)

    print(f"\n✅ Updated {WORKFLOW} with {len(health_urls)} URLs.")
    print("\nNext steps:")
    print("  git add .github/workflows/keepalive.yml")
    print("  git commit -m 'Update keepalive URLs from Render'")
    print("  git push origin master")

if __name__ == "__main__":
    main()
