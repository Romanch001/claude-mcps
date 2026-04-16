"""
Zomato MCP Server — remote HTTP/SSE endpoint for claude.ai
Endpoint: GET /sse

Backed by Google Places API (Zomato & Swiggy block DigitalOcean IPs).
Requires: GOOGLE_MAPS_API_KEY env var (same key as google-maps-mcp).
Tools:
  search_restaurants(city, query, count)
  get_restaurant_info(restaurant_name, city)
  get_restaurant_menu(restaurant_name, city)
  get_cuisines_in_city(city)
  search_by_cuisine(city, cuisine, count)
"""
import os, urllib.parse
import httpx
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.types import Tool, TextContent
from starlette.responses import JSONResponse
import uvicorn

PORT    = int(os.environ.get("PORT", 8000))
API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY", "")
BASE    = "https://maps.googleapis.com/maps/api/place"

server = Server("zomato-mcp")
sse_transport = SseServerTransport("/messages/")

PRICE_MAP = {0: "Free", 1: "₹ (Inexpensive)", 2: "₹₹ (Moderate)",
             3: "₹₹₹ (Expensive)", 4: "₹₹₹₹ (Very Expensive)"}

COMMON_CUISINES = [
    "North Indian", "South Indian", "Chinese", "Italian", "Fast Food",
    "Biryani", "Mughlai", "Street Food", "Desserts", "Bakery",
    "Continental", "Mexican", "Thai", "Japanese", "Mediterranean",
    "Pizza", "Burgers", "Rolls & Wraps", "Seafood", "Cafe",
    "Ice Cream", "Chaat", "Momos", "Sandwich", "Beverages",
]


def _no_key():
    return (
        "⚠️  GOOGLE_MAPS_API_KEY not set.\n"
        "Add it to the Droplet .env file:\n"
        "  GOOGLE_MAPS_API_KEY=your_key_here\n"
        "Then: docker compose up -d zomato-mcp"
    )


# ── Tool registry ─────────────────────────────────────────────────────────────

@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="search_restaurants",
            description=(
                "Search for restaurants by city and keyword, cuisine, or dish. "
                "Examples: 'biryani in Pune', 'best pizza in Mumbai', 'rooftop cafes in Delhi'."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "City name e.g. 'Pune', 'Mumbai', 'Delhi'"},
                    "query": {"type": "string", "description": "Restaurant name, cuisine, or dish"},
                    "count": {"type": "integer", "description": "Number of results (default 10)", "default": 10}
                },
                "required": ["city", "query"]
            }
        ),
        Tool(
            name="get_restaurant_info",
            description=(
                "Get details for a specific restaurant: Google rating, price range, "
                "address, phone number, opening hours, and website."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "restaurant_name": {"type": "string", "description": "Name of the restaurant"},
                    "city": {"type": "string", "description": "City where the restaurant is located"}
                },
                "required": ["restaurant_name", "city"]
            }
        ),
        Tool(
            name="get_restaurant_menu",
            description=(
                "Attempt to get menu information for a restaurant. "
                "Returns whatever menu data is publicly available."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "restaurant_name": {"type": "string", "description": "Name of the restaurant"},
                    "city": {"type": "string", "description": "City where the restaurant is located"}
                },
                "required": ["restaurant_name", "city"]
            }
        ),
        Tool(
            name="get_cuisines_in_city",
            description="List popular cuisine types available in a city.",
            inputSchema={
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "City name"}
                },
                "required": ["city"]
            }
        ),
        Tool(
            name="search_by_cuisine",
            description="Find top restaurants serving a specific cuisine in a city.",
            inputSchema={
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "City name"},
                    "cuisine": {"type": "string", "description": "Cuisine type e.g. 'North Indian', 'Chinese', 'Italian'"},
                    "count": {"type": "integer", "description": "Number of results (default 10)", "default": 10}
                },
                "required": ["city", "cuisine"]
            }
        )
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if not API_KEY:
        return [TextContent(type="text", text=_no_key())]

    if name == "search_restaurants":
        result = await _search(arguments["city"], arguments["query"], arguments.get("count", 10))
    elif name == "get_restaurant_info":
        result = await _get_info(arguments["restaurant_name"], arguments["city"])
    elif name == "get_restaurant_menu":
        result = await _get_menu(arguments["restaurant_name"], arguments["city"])
    elif name == "get_cuisines_in_city":
        result = _cuisines(arguments["city"])
    elif name == "search_by_cuisine":
        result = await _search(arguments["city"], arguments["cuisine"] + " restaurant", arguments.get("count", 10))
    else:
        raise ValueError(f"Unknown tool: {name}")
    return [TextContent(type="text", text=result)]


# ── Implementations ───────────────────────────────────────────────────────────

async def _text_search(query: str, city: str) -> list:
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(f"{BASE}/textsearch/json", params={
            "query": f"{query} restaurant in {city} India",
            "type": "restaurant",
            "key": API_KEY,
            "language": "en",
        })
        data = r.json()
        if data.get("status") == "OK":
            return data.get("results", [])
    return []


async def _place_details(place_id: str) -> dict:
    fields = (
        "name,rating,user_ratings_total,formatted_address,"
        "formatted_phone_number,opening_hours,price_level,"
        "website,editorial_summary,types,url"
    )
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(f"{BASE}/details/json", params={
            "place_id": place_id,
            "fields": fields,
            "key": API_KEY,
            "language": "en",
        })
        data = r.json()
        return data.get("result", {})


async def _search(city: str, query: str, count: int = 10) -> str:
    results = await _text_search(query, city)
    if not results:
        return f"🍽️ No restaurants found for '{query}' in {city.title()}."

    lines = [f"🍽️ **'{query}' in {city.title()}** ({min(len(results), count)} results)\n"]
    for p in results[:count]:
        name = p.get("name", "Unknown")
        rating = p.get("rating", "")
        n_ratings = p.get("user_ratings_total", "")
        address = p.get("formatted_address", "").replace(", India", "")
        price = PRICE_MAP.get(p.get("price_level"), "")
        open_now = p.get("opening_hours", {}).get("open_now")
        open_str = " | 🟢 Open now" if open_now is True else " | 🔴 Closed" if open_now is False else ""

        line = f"**{name}**"
        if rating:
            line += f" ⭐ {rating}"
            if n_ratings:
                line += f" ({n_ratings:,} reviews)"
        if price:
            line += f" | {price}"
        line += open_str
        if address:
            line += f"\n  📍 {address}"
        lines.append(line + "\n")
    return "\n".join(lines)


async def _get_info(restaurant_name: str, city: str) -> str:
    results = await _text_search(restaurant_name, city)
    if not results:
        return f"🍽️ '{restaurant_name}' not found in {city.title()}."

    # Pick best name match
    target = results[0]
    name_lower = restaurant_name.lower()
    for r in results:
        if name_lower in r.get("name", "").lower():
            target = r
            break

    place_id = target.get("place_id", "")
    detail = await _place_details(place_id) if place_id else target

    name = detail.get("name", restaurant_name)
    rating = detail.get("rating", "")
    n_ratings = detail.get("user_ratings_total", "")
    address = detail.get("formatted_address", "").replace(", India", "")
    phone = detail.get("formatted_phone_number", "")
    price = PRICE_MAP.get(detail.get("price_level"), "")
    website = detail.get("website", "")
    maps_url = detail.get("url", "")
    summary = (detail.get("editorial_summary") or {}).get("overview", "")
    hours = detail.get("opening_hours", {})
    open_now = hours.get("open_now")
    weekday_text = hours.get("weekday_text", [])

    lines = [f"🍽️ **{name}** — {city.title()}\n"]
    if rating:
        line = f"⭐ Rating: {rating}/5"
        if n_ratings:
            line += f" ({n_ratings:,} Google reviews)"
        lines.append(line)
    if price:
        lines.append(f"💰 Price range: {price}")
    if summary:
        lines.append(f"📝 {summary}")
    if address:
        lines.append(f"📍 Address: {address}")
    if phone:
        lines.append(f"📞 Phone: {phone}")
    if open_now is not None:
        lines.append(f"🕐 Status: {'Open now' if open_now else 'Closed now'}")
    if weekday_text:
        lines.append("🗓️ Hours:")
        for h in weekday_text:
            lines.append(f"   {h}")
    if website:
        lines.append(f"🌐 Website: {website}")
    if maps_url:
        lines.append(f"🗺️ Google Maps: {maps_url}")

    return "\n".join(lines)


async def _get_menu(restaurant_name: str, city: str) -> str:
    # Google Places doesn't provide menu data.
    # Get basic info + Zomato/Swiggy search links.
    results = await _text_search(restaurant_name, city)
    name = restaurant_name
    address = ""
    if results:
        name = results[0].get("name", restaurant_name)
        address = results[0].get("formatted_address", "")

    q = urllib.parse.quote(name)
    city_slug = city.lower().replace(" ", "-")
    return (
        f"🍽️ **{name} Menu** — {city.title()}\n\n"
        f"Menu pricing isn't available via Google Places API.\n"
        f"Check the menu directly:\n\n"
        f"• **Zomato:** https://www.zomato.com/{city_slug}/{q.lower().replace('%20','-')}-restaurant/order\n"
        f"• **Swiggy:** https://www.swiggy.com/search?query={q}\n"
        + (f"• **Google Maps:** https://www.google.com/maps/search/{q}+{urllib.parse.quote(city)}\n" if True else "")
        + (f"\n📍 {address}" if address else "")
    )


def _cuisines(city: str) -> str:
    return (
        f"🍴 **Popular cuisines in {city.title()}:**\n"
        + ", ".join(COMMON_CUISINES)
        + f"\n\nSearch any of these with search_restaurants."
    )


# ── HTTP app (raw ASGI) ───────────────────────────────────────────────────────

async def app(scope, receive, send):
    if scope["type"] == "lifespan":
        while True:
            msg = await receive()
            if msg["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif msg["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return

    if scope["type"] != "http":
        return

    path = scope.get("path", "")
    if path in ("/health", "/"):
        await JSONResponse({"status": "ok", "service": "zomato-mcp"})(scope, receive, send)
    elif path == "/sse":
        async with sse_transport.connect_sse(scope, receive, send) as streams:
            await server.run(streams[0], streams[1], server.create_initialization_options())
    elif path.startswith("/messages/"):
        await sse_transport.handle_post_message(scope, receive, send)
    else:
        await JSONResponse({"error": "not found"}, status_code=404)(scope, receive, send)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
