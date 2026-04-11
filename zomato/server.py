"""
Zomato MCP Server — remote HTTP/SSE endpoint for claude.ai
Endpoint: GET /sse

Uses Zomato's internal web APIs (no official API key required).
Tools:
  search_restaurants(city, query, cuisines)
  get_restaurant_details(restaurant_name, city)
  get_cuisines_in_city(city)
  search_by_cuisine(city, cuisine)
"""
import os
import json
import urllib.parse
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

# Zomato web headers (mimic browser)
ZOMATO_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "en-IN,en;q=0.9",
    "Referer": "https://www.zomato.com/",
    "X-Requested-With": "XMLHttpRequest",
}

# City → Zomato city id mapping (most common Indian cities)
CITY_IDS = {
    "mumbai": 3, "delhi": 1, "bangalore": 4, "bengaluru": 4,
    "hyderabad": 2, "chennai": 5, "kolkata": 6, "pune": 7,
    "jaipur": 10, "ahmedabad": 11, "lucknow": 58, "chandigarh": 15,
    "surat": 14, "nagpur": 8, "goa": 105, "bhopal": 17,
    "indore": 61, "coimbatore": 49, "kochi": 50, "visakhapatnam": 46,
    "new delhi": 1,
}

server = Server("zomato-mcp")
sse_transport = SseServerTransport("/messages/")


# ── Tool registry ─────────────────────────────────────────────────────────────

@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="search_restaurants",
            description=(
                "Search for restaurants on Zomato by city and keyword/cuisine. "
                "Examples: search pizza places in Mumbai, best biryani in Hyderabad, "
                "rooftop restaurants in Delhi."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "City name, e.g. 'Mumbai', 'Delhi', 'Bangalore'"},
                    "query": {"type": "string", "description": "Restaurant name, cuisine, or dish to search for"},
                    "count": {"type": "integer", "description": "Number of results (default 10, max 20)", "default": 10}
                },
                "required": ["city", "query"]
            }
        ),
        Tool(
            name="get_cuisines_in_city",
            description="List all available cuisines on Zomato in a given city.",
            inputSchema={
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "City name, e.g. 'Pune', 'Chennai'"}
                },
                "required": ["city"]
            }
        ),
        Tool(
            name="search_by_cuisine",
            description="Find the top restaurants serving a specific cuisine in a city.",
            inputSchema={
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "City name"},
                    "cuisine": {"type": "string", "description": "Cuisine type, e.g. 'North Indian', 'Chinese', 'Italian', 'South Indian'"},
                    "count": {"type": "integer", "description": "Number of results", "default": 10}
                },
                "required": ["city", "cuisine"]
            }
        ),
        Tool(
            name="get_restaurant_info",
            description="Get details about a specific restaurant including rating, cuisine, cost, and address.",
            inputSchema={
                "type": "object",
                "properties": {
                    "restaurant_name": {"type": "string", "description": "Name of the restaurant"},
                    "city": {"type": "string", "description": "City where the restaurant is located"}
                },
                "required": ["restaurant_name", "city"]
            }
        )
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "search_restaurants":
        result = await _search_restaurants(
            arguments["city"], arguments["query"], arguments.get("count", 10)
        )
    elif name == "get_cuisines_in_city":
        result = await _get_cuisines(arguments["city"])
    elif name == "search_by_cuisine":
        result = await _search_by_cuisine(
            arguments["city"], arguments["cuisine"], arguments.get("count", 10)
        )
    elif name == "get_restaurant_info":
        result = await _search_restaurants(
            arguments["city"], arguments["restaurant_name"], 5
        )
    else:
        raise ValueError(f"Unknown tool: {name}")
    return [TextContent(type="text", text=result)]


# ── Implementations ───────────────────────────────────────────────────────────

def _resolve_city_id(city: str) -> int:
    return CITY_IDS.get(city.lower(), 3)  # default Mumbai


async def _search_restaurants(city: str, query: str, count: int = 10) -> str:
    city_id = _resolve_city_id(city)
    encoded_query = urllib.parse.quote(query)

    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        try:
            # Use Zomato's search endpoint
            url = (
                f"https://www.zomato.com/php/search.php"
                f"?searchFor=restaurants&q={encoded_query}&cityId={city_id}"
                f"&isOrderDelivery=0&sort=popularity&order=desc"
            )
            r = await client.get(url, headers=ZOMATO_HEADERS)

            if r.status_code == 200:
                try:
                    data = r.json()
                    return _format_restaurant_search(data, city, query, count)
                except Exception:
                    pass

            # Try alternative endpoint
            url2 = f"https://www.zomato.com/webroutes/search/autoSuggest?q={encoded_query}&cityId={city_id}&latitude=&longitude=&entityId=&entityType="
            r2 = await client.get(url2, headers=ZOMATO_HEADERS)
            if r2.status_code == 200:
                try:
                    data2 = r2.json()
                    return _format_autosuggest(data2, city, query, count)
                except Exception:
                    pass

        except httpx.RequestError as e:
            pass

    # Fallback: Useful curated info
    return _fallback_zomato_info(city, query)


def _format_restaurant_search(data: dict, city: str, query: str, count: int) -> str:
    results = data.get("results", {})
    restaurants = results.get("restaurants", [])

    if not restaurants:
        return _fallback_zomato_info(city, query)

    lines = [f"🍽️  **Zomato — '{query}' in {city.title()}** ({len(restaurants)} found)\n"]
    for r in restaurants[:count]:
        info = r.get("restaurant", r)
        name = info.get("name", "Unknown")
        rating = info.get("user_rating", {}).get("aggregate_rating", "N/A")
        votes = info.get("user_rating", {}).get("votes", 0)
        cuisines = info.get("cuisines", "N/A")
        cost = info.get("average_cost_for_two", "N/A")
        address = info.get("location", {}).get("address", "")
        url = info.get("url", "")

        lines.append(
            f"**{name}** ⭐ {rating} ({votes} votes)\n"
            f"  Cuisines: {cuisines}\n"
            f"  Avg cost for 2: ₹{cost}\n"
            f"  📍 {address}\n"
            + (f"  🔗 {url}\n" if url else "")
        )
    return "\n".join(lines)


def _format_autosuggest(data: dict, city: str, query: str, count: int) -> str:
    restaurants = data.get("restaurants", [])
    if not restaurants:
        return _fallback_zomato_info(city, query)

    lines = [f"🍽️  **Zomato — '{query}' in {city.title()}**\n"]
    for r in restaurants[:count]:
        name = r.get("name", r.get("restaurantName", "Unknown"))
        cuisine = r.get("cuisine", r.get("cuisines", ""))
        rating = r.get("rating", "")
        lines.append(f"• **{name}** {('⭐ ' + str(rating)) if rating else ''} — {cuisine}")

    lines.append(f"\n🔗 Full results: https://www.zomato.com/{city.lower()}/{urllib.parse.quote(query)}-restaurants")
    return "\n".join(lines)


def _fallback_zomato_info(city: str, query: str) -> str:
    city_slug = city.lower().replace(" ", "-")
    query_slug = query.lower().replace(" ", "-")
    return (
        f"🍽️  **Zomato — {query} in {city.title()}**\n\n"
        f"Live restaurant data is best viewed at:\n"
        f"🔗 https://www.zomato.com/{city_slug}/{query_slug}-restaurants\n\n"
        f"📱 Or use the Zomato app for real-time availability, ratings, and ordering.\n\n"
        f"**Popular search tips:**\n"
        f"• Search 'biryani', 'pizza', 'chinese', 'north indian', 'south indian'\n"
        f"• Try 'best rated {query} {city}' or 'cheap {query} near me'\n"
        f"• Zomato Gold restaurants for premium dining with offers"
    )


async def _get_cuisines(city: str) -> str:
    city_id = _resolve_city_id(city)

    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        try:
            url = f"https://www.zomato.com/webroutes/getPage?page_url=/{city.lower()}/restaurants&location=&isMobile=0"
            r = await client.get(url, headers=ZOMATO_HEADERS)
            if r.status_code == 200:
                data = r.json()
                # Try to extract cuisine list
                page_data = data.get("page_data", {})
                sections = page_data.get("sections", {})
                for key, val in sections.items():
                    if "cuisine" in key.lower():
                        cuisines = val.get("entities", [])
                        if cuisines:
                            names = [c.get("friendly_url", "").replace("-", " ").title() for c in cuisines]
                            return f"🍴 **Cuisines available on Zomato in {city.title()}:**\n" + ", ".join(filter(None, names))
        except Exception:
            pass

    # Hardcoded common cuisines
    common_cuisines = [
        "North Indian", "South Indian", "Chinese", "Italian", "Fast Food",
        "Biryani", "Mughlai", "Street Food", "Desserts", "Bakery",
        "Continental", "Mexican", "Thai", "Japanese", "Mediterranean",
        "Pizza", "Burgers", "Sandwich", "Seafood", "Beverages"
    ]
    return (
        f"🍴 **Popular cuisines on Zomato in {city.title()}:**\n"
        + ", ".join(common_cuisines)
        + f"\n\n🔗 Full list: https://www.zomato.com/{city.lower()}/restaurants"
    )


async def _search_by_cuisine(city: str, cuisine: str, count: int = 10) -> str:
    return await _search_restaurants(city, cuisine, count)


# ── HTTP app ──────────────────────────────────────────────────────────────────

async def handle_sse(request: Request):
    async with sse_transport.connect_sse(
        request.scope, request.receive, request._send
    ) as streams:
        await server.run(streams[0], streams[1], server.create_initialization_options())


async def health(request: Request):
    return JSONResponse({"status": "ok", "service": "zomato-mcp"})


app = Starlette(
    routes=[
        Route("/health", health),
        Route("/sse", handle_sse),
        Mount("/messages/", app=sse_transport.handle_post_message),
    ]
)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
