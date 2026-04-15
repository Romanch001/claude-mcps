"""
Google Maps MCP Server — remote HTTP/SSE endpoint for claude.ai
Uses Google Maps Platform APIs (Geocoding, Places, Directions, Distance Matrix).

Required env var:
  GOOGLE_MAPS_API_KEY — from console.cloud.google.com (free tier: $200/month credit)
  Enable: Geocoding API, Places API, Directions API, Distance Matrix API
"""
import os
import httpx
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.types import Tool, TextContent
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route, Mount
import uvicorn

PORT = int(os.environ.get("PORT", 8000))
GOOGLE_MAPS_API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY", "")

server = Server("google-maps-mcp")
sse_transport = SseServerTransport("/messages/")

MAPS_BASE = "https://maps.googleapis.com/maps/api"


def _check_config():
    if not GOOGLE_MAPS_API_KEY:
        return (
            "⚠️  Google Maps API key not configured.\n"
            "1. Go to https://console.cloud.google.com/\n"
            "2. Create a project → Enable APIs:\n"
            "   • Geocoding API\n"
            "   • Places API\n"
            "   • Directions API\n"
            "   • Distance Matrix API\n"
            "3. Credentials → Create API Key\n"
            "4. Set GOOGLE_MAPS_API_KEY in the Render dashboard.\n"
            "Free tier: $200/month credit covers ~40,000 geocode calls."
        )
    return None


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="geocode",
            description="Convert a street address or place name to latitude/longitude coordinates.",
            inputSchema={
                "type": "object",
                "properties": {
                    "address": {"type": "string", "description": "Address or place name, e.g. 'Eiffel Tower, Paris', '1600 Amphitheatre Pkwy, Mountain View, CA'."}
                },
                "required": ["address"]
            }
        ),
        Tool(
            name="reverse_geocode",
            description="Convert latitude/longitude coordinates to a human-readable address.",
            inputSchema={
                "type": "object",
                "properties": {
                    "lat": {"type": "number", "description": "Latitude."},
                    "lng": {"type": "number", "description": "Longitude."}
                },
                "required": ["lat", "lng"]
            }
        ),
        Tool(
            name="search_places",
            description="Search for places near a location (restaurants, hotels, hospitals, ATMs, etc.).",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What to search for, e.g. 'pizza restaurant', 'ATM', 'hospital'."},
                    "location": {"type": "string", "description": "Center of search, e.g. 'Mumbai, India', 'Times Square, New York'."},
                    "radius_meters": {"type": "integer", "description": "Search radius in meters (max 50000). Default: 5000.", "default": 5000}
                },
                "required": ["query", "location"]
            }
        ),
        Tool(
            name="get_directions",
            description="Get turn-by-turn directions between two locations.",
            inputSchema={
                "type": "object",
                "properties": {
                    "origin": {"type": "string", "description": "Starting location."},
                    "destination": {"type": "string", "description": "Ending location."},
                    "mode": {"type": "string", "description": "Travel mode: 'driving', 'walking', 'bicycling', 'transit'. Default: driving.", "default": "driving"}
                },
                "required": ["origin", "destination"]
            }
        ),
        Tool(
            name="get_distance",
            description="Get the travel distance and duration between multiple origins and destinations.",
            inputSchema={
                "type": "object",
                "properties": {
                    "origins": {"type": "array", "items": {"type": "string"}, "description": "List of origin locations."},
                    "destinations": {"type": "array", "items": {"type": "string"}, "description": "List of destination locations."},
                    "mode": {"type": "string", "description": "Travel mode: 'driving', 'walking', 'bicycling', 'transit'. Default: driving.", "default": "driving"}
                },
                "required": ["origins", "destinations"]
            }
        )
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    err = _check_config()
    if err:
        return [TextContent(type="text", text=err)]

    async with httpx.AsyncClient(timeout=20) as client:
        if name == "geocode":
            result = await _geocode(client, arguments)
        elif name == "reverse_geocode":
            result = await _reverse_geocode(client, arguments)
        elif name == "search_places":
            result = await _search_places(client, arguments)
        elif name == "get_directions":
            result = await _get_directions(client, arguments)
        elif name == "get_distance":
            result = await _get_distance(client, arguments)
        else:
            raise ValueError(f"Unknown tool: {name}")
    return [TextContent(type="text", text=result)]


async def _geocode(client, args) -> str:
    r = await client.get(f"{MAPS_BASE}/geocode/json", params={"address": args.get("address", ""), "key": GOOGLE_MAPS_API_KEY})
    d = r.json()
    if d.get("status") != "OK":
        return f"Geocoding failed: {d.get('status')} — {d.get('error_message','')}"
    result = d["results"][0]
    loc = result["geometry"]["location"]
    addr = result["formatted_address"]
    comps = {c["types"][0]: c["long_name"] for c in result.get("address_components", []) if c.get("types")}
    lines = [
        f"**{addr}**",
        f"Latitude:  {loc['lat']}",
        f"Longitude: {loc['lng']}",
        f"Place ID:  {result.get('place_id','')}",
    ]
    for key in ("country", "administrative_area_level_1", "locality", "postal_code"):
        if key in comps:
            lines.append(f"{key.replace('_',' ').title()}: {comps[key]}")
    return "\n".join(lines)


async def _reverse_geocode(client, args) -> str:
    r = await client.get(f"{MAPS_BASE}/geocode/json", params={"latlng": f"{args['lat']},{args['lng']}", "key": GOOGLE_MAPS_API_KEY})
    d = r.json()
    if d.get("status") != "OK" or not d.get("results"):
        return f"Reverse geocode failed: {d.get('status')}"
    lines = [f"**Location ({args['lat']}, {args['lng']}):**\n"]
    for res in d["results"][:3]:
        lines.append(f"• {res['formatted_address']}")
    return "\n".join(lines)


async def _search_places(client, args) -> str:
    # First geocode the location
    r_geo = await client.get(f"{MAPS_BASE}/geocode/json", params={"address": args.get("location",""), "key": GOOGLE_MAPS_API_KEY})
    geo = r_geo.json()
    if geo.get("status") != "OK":
        return f"Could not geocode location '{args.get('location')}'"
    loc = geo["results"][0]["geometry"]["location"]
    latlng = f"{loc['lat']},{loc['lng']}"

    r = await client.get(
        f"{MAPS_BASE}/place/nearbysearch/json",
        params={
            "location": latlng,
            "radius": min(int(args.get("radius_meters", 5000)), 50000),
            "keyword": args.get("query", ""),
            "key": GOOGLE_MAPS_API_KEY
        }
    )
    d = r.json()
    if d.get("status") not in ("OK", "ZERO_RESULTS"):
        return f"Places search failed: {d.get('status')} — {d.get('error_message','')}"
    places = d.get("results", [])
    if not places:
        return f"No places found for '{args.get('query')}' near {args.get('location')}."
    lines = [f"**Places: '{args.get('query')}' near {args.get('location')}** ({len(places)} results)\n"]
    for p in places[:10]:
        rating = f"⭐{p['rating']}" if p.get("rating") else ""
        open_now = " (Open now)" if p.get("opening_hours", {}).get("open_now") else ""
        lines.append(
            f"• **{p['name']}** {rating}{open_now}\n"
            f"  {p.get('vicinity','')}"
        )
    return "\n".join(lines)


async def _get_directions(client, args) -> str:
    r = await client.get(
        f"{MAPS_BASE}/directions/json",
        params={
            "origin": args.get("origin", ""),
            "destination": args.get("destination", ""),
            "mode": args.get("mode", "driving"),
            "key": GOOGLE_MAPS_API_KEY
        }
    )
    d = r.json()
    if d.get("status") != "OK":
        return f"Directions failed: {d.get('status')} — {d.get('error_message','')}"
    route = d["routes"][0]
    leg = route["legs"][0]
    lines = [
        f"**Directions: {leg['start_address']} → {leg['end_address']}**",
        f"Distance: {leg['distance']['text']} | Duration: {leg['duration']['text']}",
        f"Mode: {args.get('mode','driving').title()}",
        "\n**Steps:**"
    ]
    import re
    for i, step in enumerate(leg.get("steps", []), 1):
        instr = re.sub(r"<[^>]+>", "", step.get("html_instructions", ""))
        dist = step["distance"]["text"]
        lines.append(f"{i}. {instr} ({dist})")
    return "\n".join(lines)


async def _get_distance(client, args) -> str:
    r = await client.get(
        f"{MAPS_BASE}/distancematrix/json",
        params={
            "origins": "|".join(args.get("origins", [])),
            "destinations": "|".join(args.get("destinations", [])),
            "mode": args.get("mode", "driving"),
            "key": GOOGLE_MAPS_API_KEY
        }
    )
    d = r.json()
    if d.get("status") != "OK":
        return f"Distance Matrix failed: {d.get('status')}"
    rows = d.get("rows", [])
    orig_addrs = d.get("origin_addresses", [])
    dest_addrs = d.get("destination_addresses", [])
    lines = [f"**Distance Matrix ({args.get('mode','driving')}):**\n"]
    for i, row in enumerate(rows):
        orig = orig_addrs[i] if i < len(orig_addrs) else f"Origin {i+1}"
        for j, elem in enumerate(row.get("elements", [])):
            dest = dest_addrs[j] if j < len(dest_addrs) else f"Dest {j+1}"
            if elem.get("status") == "OK":
                lines.append(f"• **{orig}** → **{dest}**\n  Distance: {elem['distance']['text']} | Duration: {elem['duration']['text']}")
            else:
                lines.append(f"• **{orig}** → **{dest}**: {elem.get('status','ERROR')}")
    return "\n".join(lines)


async def handle_sse(request: Request):
    async with sse_transport.connect_sse(
        request.scope, request.receive, request._send
    ) as streams:
        await server.run(streams[0], streams[1], server.create_initialization_options())


async def health(request: Request):
    return JSONResponse({"status": "ok", "service": "google-maps-mcp"})


app = Starlette(
    routes=[
        Route("/health", health),
        Route("/sse", handle_sse),
        Route("/messages/", sse_transport.handle_post_message),
    ]
)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
