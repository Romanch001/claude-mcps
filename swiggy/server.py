"""
Swiggy MCP Server — remote HTTP/SSE endpoint for claude.ai
Endpoint: GET /sse

Uses Swiggy's public dapi endpoints (no API key required).
Tools:
  search_restaurants(location, query)
  get_restaurants_by_location(latitude, longitude)
  search_food(query, latitude, longitude)
  get_restaurant_menu(restaurant_id, latitude, longitude)
"""
import os
import json
import urllib.parse
import httpx
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.types import Tool, TextContent
from starlette.responses import JSONResponse
import uvicorn

PORT = int(os.environ.get("PORT", 8000))

# Common city coordinates
CITY_COORDS = {
    "mumbai": (19.0760, 72.8777),
    "delhi": (28.6139, 77.2090),
    "bangalore": (12.9716, 77.5946),
    "bengaluru": (12.9716, 77.5946),
    "hyderabad": (17.3850, 78.4867),
    "chennai": (13.0827, 80.2707),
    "kolkata": (22.5726, 88.3639),
    "pune": (18.5204, 73.8567),
    "jaipur": (26.9124, 75.7873),
    "ahmedabad": (23.0225, 72.5714),
    "lucknow": (26.8467, 80.9462),
    "chandigarh": (30.7333, 76.7794),
    "surat": (21.1702, 72.8311),
    "nagpur": (21.1458, 79.0882),
    "goa": (15.2993, 74.1240),
    "bhopal": (23.2599, 77.4126),
    "indore": (22.7196, 75.8577),
    "coimbatore": (11.0168, 76.9558),
    "kochi": (9.9312, 76.2673),
    "visakhapatnam": (17.6868, 83.2185),
    "new delhi": (28.6139, 77.2090),
    "noida": (28.5355, 77.3910),
    "gurgaon": (28.4595, 77.0266),
    "gurugram": (28.4595, 77.0266),
}

SWIGGY_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "en-IN,en;q=0.9",
    "Referer": "https://www.swiggy.com/",
    "Origin": "https://www.swiggy.com",
}

server = Server("swiggy-mcp")
sse_transport = SseServerTransport("/messages/")


# ── Tool registry ─────────────────────────────────────────────────────────────

@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="search_restaurants",
            description=(
                "Search for restaurants on Swiggy by city and keyword. "
                "Find restaurants by name, cuisine type, or dish. "
                "Example: 'biryani in Hyderabad', 'pizza in Mumbai', 'chinese in Bangalore'."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "City name, e.g. 'Mumbai', 'Delhi', 'Bangalore'"},
                    "query": {"type": "string", "description": "Restaurant name, cuisine, or dish"},
                    "count": {"type": "integer", "description": "Number of results to return (default 10)", "default": 10}
                },
                "required": ["city", "query"]
            }
        ),
        Tool(
            name="browse_restaurants",
            description="Browse all available restaurants on Swiggy for a given city (top-rated, sorted by popularity).",
            inputSchema={
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "City name"},
                    "count": {"type": "integer", "description": "Number of restaurants to return (default 15)", "default": 15}
                },
                "required": ["city"]
            }
        ),
        Tool(
            name="get_restaurants_by_coordinates",
            description="Get restaurants available for Swiggy delivery at specific GPS coordinates.",
            inputSchema={
                "type": "object",
                "properties": {
                    "latitude": {"type": "number", "description": "Latitude coordinate, e.g. 19.0760"},
                    "longitude": {"type": "number", "description": "Longitude coordinate, e.g. 72.8777"},
                    "count": {"type": "integer", "description": "Number of results", "default": 15}
                },
                "required": ["latitude", "longitude"]
            }
        ),
        Tool(
            name="search_food_items",
            description="Search for specific food items/dishes across all restaurants in a city on Swiggy.",
            inputSchema={
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "City name"},
                    "dish": {"type": "string", "description": "Name of the food item or dish, e.g. 'Chicken Biryani', 'Paneer Butter Masala', 'Pasta'"}
                },
                "required": ["city", "dish"]
            }
        )
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "search_restaurants":
        result = await _search_restaurants(arguments["city"], arguments["query"], arguments.get("count", 10))
    elif name == "browse_restaurants":
        result = await _browse_restaurants(arguments["city"], arguments.get("count", 15))
    elif name == "get_restaurants_by_coordinates":
        result = await _get_by_coords(arguments["latitude"], arguments["longitude"], arguments.get("count", 15))
    elif name == "search_food_items":
        result = await _search_food(arguments["city"], arguments["dish"])
    else:
        raise ValueError(f"Unknown tool: {name}")
    return [TextContent(type="text", text=result)]


# ── Implementations ───────────────────────────────────────────────────────────

def _resolve_coords(city: str):
    return CITY_COORDS.get(city.lower(), (12.9716, 77.5946))  # default Bangalore


async def _fetch_swiggy_restaurants(lat: float, lng: float, count: int) -> list:
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        try:
            url = (
                f"https://www.swiggy.com/dapi/restaurants/list/v5"
                f"?lat={lat}&lng={lng}&is-seo-homepage-enabled=true&page_type=DESKTOP_WEB_LISTING"
            )
            r = await client.get(url, headers=SWIGGY_HEADERS)
            if r.status_code == 200:
                data = r.json()
                # Navigate to restaurant cards
                cards = (
                    data.get("data", {})
                    .get("cards", [])
                )
                restaurants = []
                for card in cards:
                    card_data = card.get("card", {}).get("card", {})
                    gridElements = card_data.get("gridElements", {})
                    info_with_style = gridElements.get("infoWithStyle", {})
                    rests = info_with_style.get("restaurants", [])
                    for r_item in rests:
                        info = r_item.get("info", {})
                        if info:
                            restaurants.append(info)
                return restaurants[:count]
        except Exception:
            pass
    return []


async def _browse_restaurants(city: str, count: int = 15) -> str:
    lat, lng = _resolve_coords(city)
    restaurants = await _fetch_swiggy_restaurants(lat, lng, count)

    if not restaurants:
        return _fallback_swiggy(city, "")

    lines = [f"🍔 **Swiggy — Restaurants in {city.title()}** (sorted by popularity)\n"]
    for r in restaurants:
        name = r.get("name", "Unknown")
        rating = r.get("avgRating", "N/A")
        delivery_time = r.get("sla", {}).get("deliveryTime", "N/A")
        cost_str = r.get("costForTwo", "")
        cuisines = r.get("cuisines", [])
        area = r.get("areaName", "")

        lines.append(
            f"**{name}** ⭐ {rating}\n"
            f"  🍴 {', '.join(cuisines[:4]) if cuisines else 'Various'}\n"
            f"  📍 {area} | 🕐 {delivery_time} mins | 💰 {cost_str}\n"
        )

    lines.append(f"\n📱 Order at: https://www.swiggy.com/city/{city.lower()}")
    return "\n".join(lines)


async def _get_by_coords(lat: float, lng: float, count: int = 15) -> str:
    restaurants = await _fetch_swiggy_restaurants(lat, lng, count)

    if not restaurants:
        return f"No Swiggy restaurants found at coordinates ({lat}, {lng}). Try a city name instead."

    lines = [f"🍔 **Swiggy — Restaurants near ({lat:.4f}, {lng:.4f})**\n"]
    for r in restaurants:
        name = r.get("name", "Unknown")
        rating = r.get("avgRating", "N/A")
        delivery_time = r.get("sla", {}).get("deliveryTime", "N/A")
        cost_str = r.get("costForTwo", "")
        cuisines = r.get("cuisines", [])
        area = r.get("areaName", "")

        lines.append(
            f"**{name}** ⭐ {rating} | {', '.join(cuisines[:3])} | "
            f"📍 {area} | 🕐 {delivery_time} min | 💰 {cost_str}"
        )

    return "\n".join(lines)


async def _search_restaurants(city: str, query: str, count: int = 10) -> str:
    lat, lng = _resolve_coords(city)
    encoded_query = urllib.parse.quote(query)

    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        try:
            # Swiggy search API
            url = (
                f"https://www.swiggy.com/dapi/restaurants/search/v3"
                f"?cityId=&lat={lat}&lng={lng}&str={encoded_query}"
                f"&trackingId=undefined&submitAction=ENTER&queryUniqueId="
            )
            r = await client.get(url, headers=SWIGGY_HEADERS)

            if r.status_code == 200:
                data = r.json()
                # Extract restaurants from search results
                results = (
                    data.get("data", {})
                    .get("cards", [{}])[0]
                    .get("groupedCard", {})
                    .get("cardGroupMap", {})
                    .get("RESTAURANT", {})
                    .get("cards", [])
                )

                restaurants = []
                for card in results:
                    info = (
                        card.get("card", {})
                        .get("card", {})
                        .get("info", {})
                    )
                    if info and info.get("name"):
                        restaurants.append(info)

                if restaurants:
                    lines = [f"🍔 **Swiggy — '{query}' in {city.title()}** ({len(restaurants)} results)\n"]
                    for r_info in restaurants[:count]:
                        name = r_info.get("name", "")
                        rating = r_info.get("avgRating", "N/A")
                        delivery_time = r_info.get("sla", {}).get("deliveryTime", "N/A")
                        cost_str = r_info.get("costForTwo", "")
                        cuisines = r_info.get("cuisines", [])
                        area = r_info.get("areaName", "")

                        lines.append(
                            f"**{name}** ⭐ {rating}\n"
                            f"  🍴 {', '.join(cuisines[:4]) if cuisines else 'Various'}\n"
                            f"  📍 {area} | 🕐 {delivery_time} mins | 💰 {cost_str}\n"
                        )

                    lines.append(f"📱 Order: https://www.swiggy.com/search?query={encoded_query}")
                    return "\n".join(lines)

        except Exception:
            pass

    # Try browsing and mentioning the query
    all_restaurants = await _fetch_swiggy_restaurants(lat, lng, 30)
    matching = [
        r for r in all_restaurants
        if query.lower() in r.get("name", "").lower()
        or any(query.lower() in c.lower() for c in r.get("cuisines", []))
    ]

    if matching:
        lines = [f"🍔 **Swiggy — '{query}' in {city.title()}**\n"]
        for r in matching[:count]:
            name = r.get("name", "")
            rating = r.get("avgRating", "N/A")
            delivery_time = r.get("sla", {}).get("deliveryTime", "N/A")
            cost_str = r.get("costForTwo", "")
            cuisines = r.get("cuisines", [])
            lines.append(
                f"**{name}** ⭐ {rating} | {', '.join(cuisines[:3])} | "
                f"🕐 {delivery_time} min | {cost_str}"
            )
        return "\n".join(lines)

    if all_restaurants:
        # Return popular restaurants and note the query
        lines = [f"🍔 **Swiggy in {city.title()}** (Showing popular restaurants — no exact match for '{query}')\n"]
        for r in all_restaurants[:8]:
            name = r.get("name", "")
            rating = r.get("avgRating", "N/A")
            cuisines = r.get("cuisines", [])
            lines.append(f"• **{name}** ⭐ {rating} — {', '.join(cuisines[:3])}")
        lines.append(f"\n📱 Search '{query}': https://www.swiggy.com/search?query={urllib.parse.quote(query)}")
        return "\n".join(lines)

    return _fallback_swiggy(city, query)


async def _search_food(city: str, dish: str) -> str:
    return await _search_restaurants(city, dish, 10)


def _fallback_swiggy(city: str, query: str) -> str:
    city_slug = city.lower().replace(" ", "-")
    q_str = f" for '{query}'" if query else ""
    return (
        f"🍔 **Swiggy{q_str} in {city.title()}**\n\n"
        f"📱 Order directly: https://www.swiggy.com/city/{city_slug}\n"
        f"🔍 Search{' ' + query if query else ''}: https://www.swiggy.com/search?query={urllib.parse.quote(query or city)}\n\n"
        f"Swiggy is available in 500+ cities across India.\n"
        f"Use the coordinates tool if you know your lat/lng for more precise results."
    )


# ── HTTP app (raw ASGI dispatcher — avoids starlette 1.x None-return bug) ────

async def app(scope, receive, send):
    """
    Raw ASGI dispatcher. We bypass Starlette's Route wrapper because the MCP
    SSE transport writes directly to `send` and never returns a Response object.
    Starlette 1.0 requires Route handlers to return a Response, which causes
    a TypeError when the handler returns None. Using raw ASGI avoids this.
    """
    if scope["type"] == "lifespan":
        # Handle lifespan events (startup/shutdown)
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return

    if scope["type"] != "http":
        return

    path = scope.get("path", "")

    if path == "/health" or path == "/":
        response = JSONResponse({"status": "ok", "service": "swiggy-mcp"})
        await response(scope, receive, send)

    elif path == "/sse":
        async with sse_transport.connect_sse(scope, receive, send) as streams:
            await server.run(
                streams[0], streams[1], server.create_initialization_options()
            )

    elif path.startswith("/messages/"):
        await sse_transport.handle_post_message(scope, receive, send)

    else:
        response = JSONResponse({"error": "not found"}, status_code=404)
        await response(scope, receive, send)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
