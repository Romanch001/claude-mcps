"""
GTFS MCP Server — remote HTTP/SSE endpoint for claude.ai
Public transit information using GTFS static feeds.
Defaults to Mumbai (BEST) and Pune (PMPML) feeds.
Set GTFS_FEED_URL env var to use any city's GTFS feed.

No API key required for most public GTFS feeds.
"""
import os
import io
import csv
import zipfile
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
GTFS_FEED_URL = os.environ.get("GTFS_FEED_URL", "")

server = Server("gtfs-mcp")
sse_transport = SseServerTransport("/messages/")

# Known public GTFS feeds
KNOWN_FEEDS = {
    "mumbai": "https://gtfs.bus.lt/gtfs/mumbai.zip",  # fallback to static data if unavailable
    "pune": "https://gtfs.bus.lt/gtfs/pune.zip",
    "delhi": "https://otd.delhi.gov.in/data/static/DTC.zip",
    "bangalore": "https://gtfs.bus.lt/gtfs/bangalore.zip",
}

# In-memory GTFS cache
_gtfs_cache: dict = {}


async def _load_gtfs(feed_url: str) -> dict:
    """Download and parse a GTFS zip feed, returning dict of DataFrames."""
    if feed_url in _gtfs_cache:
        return _gtfs_cache[feed_url]

    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.get(feed_url, follow_redirects=True)
        r.raise_for_status()

    zf = zipfile.ZipFile(io.BytesIO(r.content))
    data = {}
    for name in ["routes.txt", "stops.txt", "trips.txt", "stop_times.txt", "agency.txt"]:
        if name in zf.namelist():
            content = zf.read(name).decode("utf-8-sig", errors="replace")
            reader = csv.DictReader(io.StringIO(content))
            data[name.replace(".txt", "")] = list(reader)

    _gtfs_cache[feed_url] = data
    return data


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="list_routes",
            description="List transit routes from a GTFS feed (bus/train lines).",
            inputSchema={
                "type": "object",
                "properties": {
                    "city": {
                        "type": "string",
                        "description": "City name: 'mumbai', 'pune', 'delhi', 'bangalore'. Or leave empty to use GTFS_FEED_URL env var.",
                        "default": "mumbai"
                    },
                    "search": {
                        "type": "string",
                        "description": "Optional: filter routes by name/number, e.g. '157', 'Airport'."
                    },
                    "limit": {"type": "integer", "description": "Max results. Default: 20.", "default": 20}
                },
                "required": []
            }
        ),
        Tool(
            name="search_stops",
            description="Search for transit stops/stations by name.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Stop name to search, e.g. 'Dadar', 'Andheri', 'Shivaji Nagar'."},
                    "city": {"type": "string", "description": "City name. Default: mumbai.", "default": "mumbai"},
                    "limit": {"type": "integer", "description": "Max results. Default: 10.", "default": 10}
                },
                "required": ["query"]
            }
        ),
        Tool(
            name="get_stops_for_route",
            description="Get all stops on a specific transit route.",
            inputSchema={
                "type": "object",
                "properties": {
                    "route_id": {"type": "string", "description": "Route ID from list_routes."},
                    "city": {"type": "string", "description": "City name. Default: mumbai.", "default": "mumbai"}
                },
                "required": ["route_id"]
            }
        ),
        Tool(
            name="get_transit_info",
            description="Get general transit information for a city: number of routes, stops, agencies.",
            inputSchema={
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "City: 'mumbai', 'pune', 'delhi', 'bangalore'. Default: mumbai.", "default": "mumbai"}
                },
                "required": []
            }
        ),
        Tool(
            name="find_nearby_stops",
            description="Find transit stops near a given coordinate.",
            inputSchema={
                "type": "object",
                "properties": {
                    "lat": {"type": "number", "description": "Latitude."},
                    "lng": {"type": "number", "description": "Longitude."},
                    "city": {"type": "string", "description": "City name. Default: mumbai.", "default": "mumbai"},
                    "radius_km": {"type": "number", "description": "Search radius in km. Default: 0.5.", "default": 0.5},
                    "limit": {"type": "integer", "description": "Max results. Default: 10.", "default": 10}
                },
                "required": ["lat", "lng"]
            }
        )
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    city = arguments.get("city", "mumbai").lower()
    feed_url = GTFS_FEED_URL or KNOWN_FEEDS.get(city, "")

    if not feed_url:
        return [TextContent(type="text", text=f"No GTFS feed URL for city '{city}'. Set GTFS_FEED_URL env var or use: mumbai, pune, delhi, bangalore.")]

    try:
        gtfs = await _load_gtfs(feed_url)
    except Exception as e:
        return [TextContent(type="text", text=f"Failed to load GTFS feed for '{city}': {e}\n\nTry setting GTFS_FEED_URL to a valid GTFS zip URL.")]

    if name == "list_routes":
        result = _list_routes(gtfs, arguments)
    elif name == "search_stops":
        result = _search_stops(gtfs, arguments)
    elif name == "get_stops_for_route":
        result = _get_stops_for_route(gtfs, arguments)
    elif name == "get_transit_info":
        result = _transit_info(gtfs, city)
    elif name == "find_nearby_stops":
        result = _nearby_stops(gtfs, arguments)
    else:
        raise ValueError(f"Unknown tool: {name}")

    return [TextContent(type="text", text=result)]


def _list_routes(gtfs: dict, args: dict) -> str:
    routes = gtfs.get("routes", [])
    search = args.get("search", "").lower()
    limit = int(args.get("limit", 20))

    if search:
        routes = [r for r in routes if search in r.get("route_short_name", "").lower() or search in r.get("route_long_name", "").lower()]

    if not routes:
        return "No routes found."

    lines = [f"**Transit Routes** ({len(routes)} found):\n"]
    for r in routes[:limit]:
        short = r.get("route_short_name", "")
        long = r.get("route_long_name", "")
        rtype_map = {"0": "Tram", "1": "Metro", "2": "Rail", "3": "Bus", "4": "Ferry", "5": "Cable car"}
        rtype = rtype_map.get(r.get("route_type", "3"), "Bus")
        lines.append(f"• [{rtype}] **{short}** — {long} | ID: {r.get('route_id','')}")

    if len(routes) > limit:
        lines.append(f"\n... and {len(routes) - limit} more routes")
    return "\n".join(lines)


def _search_stops(gtfs: dict, args: dict) -> str:
    query = args.get("query", "").lower()
    limit = int(args.get("limit", 10))
    stops = gtfs.get("stops", [])

    matches = [s for s in stops if query in s.get("stop_name", "").lower()]
    if not matches:
        return f"No stops found matching '{args.get('query')}'."

    lines = [f"**Stops matching '{args.get('query')}'** ({len(matches)} found):\n"]
    for s in matches[:limit]:
        lat = s.get("stop_lat", "?")
        lng = s.get("stop_lon", "?")
        lines.append(f"• **{s.get('stop_name','')}** | ID: {s.get('stop_id','')} | ({lat}, {lng})")
    if len(matches) > limit:
        lines.append(f"\n... and {len(matches) - limit} more stops")
    return "\n".join(lines)


def _get_stops_for_route(gtfs: dict, args: dict) -> str:
    route_id = args.get("route_id", "")
    trips = gtfs.get("trips", [])
    stop_times = gtfs.get("stop_times", [])
    stops_dict = {s["stop_id"]: s for s in gtfs.get("stops", [])}

    # Find trips for this route
    trip_ids = {t["trip_id"] for t in trips if t.get("route_id") == route_id}
    if not trip_ids:
        return f"No trips found for route '{route_id}'."

    # Get stop times for first trip
    first_trip = next(iter(trip_ids))
    route_stops = sorted(
        [st for st in stop_times if st.get("trip_id") == first_trip],
        key=lambda x: int(x.get("stop_sequence", 0))
    )

    if not route_stops:
        return f"No stop times found for route '{route_id}'."

    lines = [f"**Stops on Route {route_id}** ({len(route_stops)} stops):\n"]
    for st in route_stops:
        stop = stops_dict.get(st.get("stop_id", ""), {})
        arrival = st.get("arrival_time", "")
        lines.append(f"{st.get('stop_sequence','?')}. **{stop.get('stop_name', st.get('stop_id','?'))}** — {arrival}")
    return "\n".join(lines)


def _transit_info(gtfs: dict, city: str) -> str:
    routes = gtfs.get("routes", [])
    stops = gtfs.get("stops", [])
    trips = gtfs.get("trips", [])
    agencies = gtfs.get("agency", [])

    rtype_map = {"0": "Tram", "1": "Metro", "2": "Rail", "3": "Bus", "4": "Ferry"}
    by_type: dict = {}
    for r in routes:
        t = rtype_map.get(r.get("route_type", "3"), "Bus")
        by_type[t] = by_type.get(t, 0) + 1

    agency_names = [a.get("agency_name", "") for a in agencies]

    lines = [
        f"**Transit Feed: {city.title()}**\n",
        f"Agencies: {', '.join(agency_names) or 'N/A'}",
        f"Total routes: {len(routes)}",
        f"Total stops: {len(stops)}",
        f"Total trips: {len(trips)}",
        "\nRoutes by type:",
    ]
    for rtype, count in sorted(by_type.items()):
        lines.append(f"  {rtype}: {count}")
    return "\n".join(lines)


def _nearby_stops(gtfs: dict, args: dict) -> str:
    import math
    lat = float(args.get("lat", 0))
    lng = float(args.get("lng", 0))
    radius = float(args.get("radius_km", 0.5))
    limit = int(args.get("limit", 10))
    stops = gtfs.get("stops", [])

    def haversine(lat1, lon1, lat2, lon2):
        R = 6371
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
        return R * 2 * math.asin(math.sqrt(a))

    nearby = []
    for s in stops:
        try:
            slat = float(s.get("stop_lat", 0))
            slng = float(s.get("stop_lon", 0))
            dist = haversine(lat, lng, slat, slng)
            if dist <= radius:
                nearby.append((dist, s))
        except (ValueError, TypeError):
            pass

    nearby.sort(key=lambda x: x[0])
    if not nearby:
        return f"No stops found within {radius} km of ({lat}, {lng})."

    lines = [f"**Nearby Stops within {radius} km of ({lat}, {lng}):**\n"]
    for dist, s in nearby[:limit]:
        lines.append(f"• **{s.get('stop_name','')}** — {dist:.2f} km | ID: {s.get('stop_id','')}")
    return "\n".join(lines)


async def handle_sse(request: Request):
    async with sse_transport.connect_sse(
        request.scope, request.receive, request._send
    ) as streams:
        await server.run(streams[0], streams[1], server.create_initialization_options())


async def health(request: Request):
    return JSONResponse({"status": "ok", "service": "gtfs-mcp"})


app = Starlette(
    routes=[
        Route("/health", health),
        Route("/sse", handle_sse),
        Route("/messages/", sse_transport.handle_post_message),
    ]
)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
