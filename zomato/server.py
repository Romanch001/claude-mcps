"""
Zomato MCP Server — remote HTTP/SSE endpoint for claude.ai
Endpoint: GET /sse

Scrapes Zomato's __NEXT_DATA__ JSON embedded in HTML pages (no API key required).
Tools:
  search_restaurants(city, query, count)
  get_restaurant_info(restaurant_name, city)
  get_restaurant_menu(restaurant_name, city)
  get_cuisines_in_city(city)
  search_by_cuisine(city, cuisine, count)
"""
import os, json, re, urllib.parse
import httpx
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.types import Tool, TextContent
from starlette.responses import JSONResponse
import uvicorn

PORT = int(os.environ.get("PORT", 8000))

ZOMATO_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9,hi;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
}

CITY_SLUGS = {
    "mumbai": "mumbai", "delhi": "delhi", "bangalore": "bangalore",
    "bengaluru": "bangalore", "hyderabad": "hyderabad", "chennai": "chennai",
    "kolkata": "kolkata", "pune": "pune", "jaipur": "jaipur",
    "ahmedabad": "ahmedabad", "lucknow": "lucknow", "chandigarh": "chandigarh",
    "surat": "surat", "nagpur": "nagpur", "goa": "goa", "bhopal": "bhopal",
    "indore": "indore", "coimbatore": "coimbatore", "kochi": "kochi",
    "visakhapatnam": "visakhapatnam", "new delhi": "delhi",
    "noida": "delhi/noida", "gurgaon": "delhi/gurgaon", "gurugram": "delhi/gurgaon",
}

server = Server("zomato-mcp")
sse_transport = SseServerTransport("/messages/")


def _city_slug(city: str) -> str:
    return CITY_SLUGS.get(city.lower(), city.lower())


async def _fetch_next_data(url: str, client: httpx.AsyncClient) -> dict:
    """Fetch a Zomato page and extract __NEXT_DATA__ JSON."""
    try:
        r = await client.get(url, headers=ZOMATO_HEADERS)
        if r.status_code == 200:
            m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text, re.DOTALL)
            if m:
                return json.loads(m.group(1))
    except Exception:
        pass
    return {}


def _walk(obj, *keys):
    """Safely walk nested dict/list."""
    for k in keys:
        if obj is None:
            return None
        if isinstance(obj, dict):
            obj = obj.get(k)
        elif isinstance(obj, list) and isinstance(k, int):
            obj = obj[k] if k < len(obj) else None
        else:
            return None
    return obj


# ── Tool registry ─────────────────────────────────────────────────────────────

@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="search_restaurants",
            description=(
                "Search for restaurants on Zomato by city and keyword, cuisine, or dish. "
                "Examples: 'biryani in Pune', 'best pizza in Mumbai', 'rooftop in Delhi'."
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
                "Get details about a specific restaurant on Zomato: rating, cuisine, cost for two, "
                "address, timings, and delivery info."
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
                "Get the menu with item names and prices for a specific restaurant on Zomato. "
                "Returns categories, dish names, and prices where available."
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
            description="List all available cuisine types on Zomato in a given city.",
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
            description="Find top restaurants serving a specific cuisine in a city on Zomato.",
            inputSchema={
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "City name"},
                    "cuisine": {"type": "string", "description": "Cuisine type e.g. 'North Indian', 'Chinese', 'Italian'"},
                    "count": {"type": "integer", "description": "Number of results", "default": 10}
                },
                "required": ["city", "cuisine"]
            }
        )
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "search_restaurants":
        result = await _search_restaurants(arguments["city"], arguments["query"], arguments.get("count", 10))
    elif name == "get_restaurant_info":
        result = await _get_restaurant_info(arguments["restaurant_name"], arguments["city"])
    elif name == "get_restaurant_menu":
        result = await _get_restaurant_menu(arguments["restaurant_name"], arguments["city"])
    elif name == "get_cuisines_in_city":
        result = _get_cuisines(arguments["city"])
    elif name == "search_by_cuisine":
        result = await _search_restaurants(arguments["city"], arguments["cuisine"], arguments.get("count", 10))
    else:
        raise ValueError(f"Unknown tool: {name}")
    return [TextContent(type="text", text=result)]


# ── Implementations ───────────────────────────────────────────────────────────

def _extract_restaurant_list(data: dict) -> list:
    """Try various __NEXT_DATA__ paths to find a restaurant list."""
    if not data:
        return []
    try:
        # Common path in Zomato listing pages
        page_data = _walk(data, "props", "pageProps", "pageData")
        if not page_data:
            page_data = _walk(data, "props", "pageProps")

        # Path 1: sections > SECTION_SEARCH_RESULT or similar
        sections = (page_data or {}).get("sections", {})
        for key, val in sections.items():
            if isinstance(val, dict):
                restaurants = val.get("entities", val.get("restaurants", []))
                if restaurants and isinstance(restaurants, list) and len(restaurants) > 0:
                    return restaurants

        # Path 2: direct list
        restaurants = (page_data or {}).get("restaurants", [])
        if restaurants:
            return restaurants

        # Path 3: search response cards
        cards = (page_data or {}).get("cards", [])
        result = []
        for card in cards:
            info = _walk(card, "card", "card", "info") or _walk(card, "info") or {}
            if info and info.get("name"):
                result.append({"info": info})
        return result

    except Exception:
        pass
    return []


def _norm_restaurant(r: dict) -> dict:
    """Normalize restaurant data regardless of nesting."""
    info = r.get("info", r.get("restaurant", r))
    if isinstance(info, dict) and info.get("name"):
        return info
    return r


def _format_restaurant_list(restaurants: list, city: str, query: str) -> str:
    lines = [f"🍽️ **Zomato — '{query}' in {city.title()}** ({len(restaurants)} found)\n"]
    for r in restaurants:
        info = _norm_restaurant(r)
        name = info.get("name", "Unknown")
        rating = info.get("avgRating", "")
        cuisines = info.get("cuisine", info.get("cuisines", ""))
        if isinstance(cuisines, list):
            cuisines = ", ".join(cuisines[:4])
        cost = info.get("costForTwo", info.get("average_cost_for_two", ""))
        area = info.get("locality", info.get("areaName", ""))
        delivery_time = _walk(info, "sla", "deliveryTime") or info.get("deliveryTime", "")

        line = f"**{name}**"
        if rating:
            line += f" ⭐ {rating}"
        if cuisines:
            line += f" | {cuisines}"
        if cost:
            line += f" | ₹{cost} for 2"
        if area:
            line += f"\n  📍 {area}"
        if delivery_time:
            line += f" | 🕐 {delivery_time} min"
        lines.append(line + "\n")
    return "\n".join(lines)


async def _search_restaurants(city: str, query: str, count: int = 10) -> str:
    slug = _city_slug(city)
    q_slug = re.sub(r"[^a-z0-9]+", "-", query.lower()).strip("-")
    q_enc = urllib.parse.quote(query)

    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        # Try listing page with __NEXT_DATA__
        url = f"https://www.zomato.com/{slug}/{q_slug}-restaurants"
        data = await _fetch_next_data(url, client)
        restaurants = _extract_restaurant_list(data)
        if restaurants:
            return _format_restaurant_list(restaurants[:count], city, query)

        # Try search API
        try:
            api_url = (
                f"https://www.zomato.com/webroutes/search/top"
                f"?searchFor=restaurants&q={q_enc}&city={slug}&isOrderDelivery=0"
            )
            r = await client.get(api_url, headers={**ZOMATO_HEADERS, "Accept": "application/json"})
            if r.status_code == 200:
                api_data = r.json()
                rests = (
                    _walk(api_data, "results", "restaurants") or
                    _walk(api_data, "restaurants") or []
                )
                if rests:
                    return _format_restaurant_list(rests[:count], city, query)
        except Exception:
            pass

    return (
        f"🍽️ **Zomato — '{query}' in {city.title()}**\n\n"
        f"Browse: https://www.zomato.com/{slug}/{q_slug}-restaurants"
    )


async def _get_restaurant_info(restaurant_name: str, city: str) -> str:
    slug = _city_slug(city)
    name_slug = re.sub(r"[^a-z0-9]+", "-", restaurant_name.lower()).strip("-")

    async with httpx.AsyncClient(timeout=25, follow_redirects=True) as client:
        # Try the restaurant info page directly
        for suffix in ["-restaurant", "-delivery", ""]:
            url = f"https://www.zomato.com/{slug}/{name_slug}{suffix}"
            data = await _fetch_next_data(url, client)
            info = _extract_single_restaurant(data)
            if info:
                return _format_restaurant_details(info, city)

        # Fall back to search and return first match
        search_url = f"https://www.zomato.com/{slug}/{name_slug}-restaurants"
        data = await _fetch_next_data(search_url, client)
        restaurants = _extract_restaurant_list(data)
        if restaurants:
            # Find best name match
            target = None
            name_lower = restaurant_name.lower()
            for r in restaurants:
                info = _norm_restaurant(r)
                if name_lower in info.get("name", "").lower():
                    target = info
                    break
            if not target:
                target = _norm_restaurant(restaurants[0])
            if target:
                return _format_restaurant_details(target, city)

    return (
        f"🍽️ **{restaurant_name}** in {city.title()}\n\n"
        f"View on Zomato: https://www.zomato.com/{slug}/{name_slug}-restaurant\n"
        f"Search: https://www.zomato.com/{slug}/{name_slug}-restaurants"
    )


def _extract_single_restaurant(data: dict) -> dict:
    """Extract single restaurant details from a restaurant page's __NEXT_DATA__."""
    try:
        page_props = _walk(data, "props", "pageProps") or {}
        # Path 1: res_response > page_data
        res = page_props.get("res_response") or page_props.get("resData") or {}
        sections = res.get("sections", {})
        basic = sections.get("SECTION_BASIC_INFO") or sections.get("basic") or {}
        if basic and basic.get("name"):
            return basic

        # Path 2: pageData sections
        page_data = page_props.get("pageData", {})
        for key in ("SECTION_BASIC_INFO", "basic_info", "restaurant"):
            val = (page_data.get("sections") or {}).get(key, {})
            if val and val.get("name"):
                return val

        # Path 3: direct restaurant object
        r = page_props.get("restaurant") or page_props.get("restaurantDetails") or {}
        if r and r.get("name"):
            return r
    except Exception:
        pass
    return {}


def _format_restaurant_details(info: dict, city: str) -> str:
    name = info.get("name", "Unknown Restaurant")
    rating = info.get("avgRating", info.get("aggregate_rating", "N/A"))
    votes = info.get("ratingCount", info.get("votes", ""))
    cuisines = info.get("cuisine", info.get("cuisines", ""))
    if isinstance(cuisines, list):
        cuisines = ", ".join(cuisines)
    cost = info.get("costForTwo", info.get("average_cost_for_two", ""))
    area = info.get("locality", info.get("areaName", ""))
    address = info.get("address", "")
    timing = info.get("timing", info.get("timings", ""))
    delivery_time = _walk(info, "sla", "deliveryTime") or info.get("deliveryTime", "")
    veg_only = info.get("veg", False)
    phone = info.get("phoneNumbers", info.get("phone", ""))

    lines = [f"🍽️ **{name}** — {city.title()}\n"]
    if rating and rating != "N/A":
        line = f"⭐ Rating: {rating}"
        if votes:
            line += f" ({votes} votes)"
        lines.append(line)
    if cuisines:
        lines.append(f"🍴 Cuisines: {cuisines}")
    if cost:
        lines.append(f"💰 Cost for 2: ₹{cost}")
    if veg_only:
        lines.append("🌿 Pure Veg")
    if area:
        lines.append(f"📍 Area: {area}")
    if address:
        lines.append(f"🏠 Address: {address}")
    if timing:
        lines.append(f"🕐 Timings: {timing}")
    if delivery_time:
        lines.append(f"🛵 Delivery: {delivery_time} min")
    if phone:
        lines.append(f"📞 Phone: {phone}")

    return "\n".join(lines)


async def _get_restaurant_menu(restaurant_name: str, city: str) -> str:
    slug = _city_slug(city)
    name_slug = re.sub(r"[^a-z0-9]+", "-", restaurant_name.lower()).strip("-")

    async with httpx.AsyncClient(timeout=25, follow_redirects=True) as client:
        # The order/delivery page has menu data in __NEXT_DATA__
        for suffix in ["/order", "-delivery", ""]:
            url = f"https://www.zomato.com/{slug}/{name_slug}-restaurant{suffix}"
            data = await _fetch_next_data(url, client)
            menu = _extract_menu(data)
            if menu:
                return _format_menu(restaurant_name, city, menu)

    return (
        f"🍽️ **{restaurant_name} Menu** — {city.title()}\n\n"
        f"Full menu with prices: https://www.zomato.com/{slug}/{name_slug}-restaurant/order\n\n"
        f"Note: Zomato requires a browser session to load menu prices interactively."
    )


def _extract_menu(data: dict) -> list:
    """Extract menu categories from __NEXT_DATA__."""
    try:
        page_props = _walk(data, "props", "pageProps") or {}
        # Path 1: order page menu
        menu = page_props.get("menu") or []
        if menu:
            return menu

        # Path 2: sections
        page_data = page_props.get("pageData", {})
        sections = page_data.get("sections", {})
        for key in ("SECTION_ORDER_FOOD", "menu", "MENU"):
            val = sections.get(key)
            if val:
                if isinstance(val, list):
                    return val
                cats = val.get("categories") or val.get("items") or val.get("menus") or []
                if cats:
                    return cats

        # Path 3: res_response
        res = page_props.get("res_response") or {}
        menu2 = res.get("menu") or res.get("menus") or []
        if menu2:
            return menu2
    except Exception:
        pass
    return []


def _format_menu(restaurant_name: str, city: str, menu: list) -> str:
    lines = [f"🍽️ **{restaurant_name} Menu** — {city.title()}\n"]
    item_count = 0

    for category in menu[:20]:
        cat_name = category.get("name") or category.get("category") or category.get("type") or ""
        items = (
            category.get("items") or category.get("dishes") or
            category.get("itemCards") or []
        )
        if not items:
            continue

        if cat_name:
            lines.append(f"\n**{cat_name}**")

        for item in items[:15]:
            # Handle itemCards wrapper
            if "card" in item:
                item = _walk(item, "card", "info") or item
            name_val = item.get("name") or item.get("itemName") or ""
            price = item.get("price") or item.get("cost") or item.get("defaultPrice") or ""
            if price:
                try:
                    price = f"₹{int(price) // 100}" if int(price) > 1000 else f"₹{price}"
                except Exception:
                    price = f"₹{price}"
            desc = (item.get("description") or item.get("desc") or "")[:80]
            is_veg = item.get("isVeg", item.get("veg"))

            if name_val:
                veg_icon = "🟢" if is_veg == 1 or is_veg is True else "🔴" if is_veg == 0 or is_veg is False else ""
                line = f"  {veg_icon} **{name_val}**"
                if price:
                    line += f" — {price}"
                lines.append(line)
                if desc:
                    lines.append(f"    _{desc}_")
                item_count += 1

    if item_count == 0:
        return ""

    lines.append(f"\n_({item_count} items shown)_")
    return "\n".join(lines)


def _get_cuisines(city: str) -> str:
    common = [
        "North Indian", "South Indian", "Chinese", "Italian", "Fast Food",
        "Biryani", "Mughlai", "Street Food", "Desserts", "Bakery",
        "Continental", "Mexican", "Thai", "Japanese", "Mediterranean",
        "Pizza", "Burgers", "Rolls & Wraps", "Seafood", "Cafe",
        "Ice Cream", "Chaat", "Momos", "Sandwich", "Beverages"
    ]
    slug = _city_slug(city)
    return (
        f"🍴 **Popular cuisines on Zomato in {city.title()}:**\n"
        + ", ".join(common)
        + f"\n\n🔗 Browse: https://www.zomato.com/{slug}/restaurants"
    )


# ── HTTP app (raw ASGI — avoids Starlette None-return bug) ────────────────────

async def app(scope, receive, send):
    if scope["type"] == "lifespan":
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

    if path in ("/health", "/"):
        response = JSONResponse({"status": "ok", "service": "zomato-mcp"})
        await response(scope, receive, send)
    elif path == "/sse":
        async with sse_transport.connect_sse(scope, receive, send) as streams:
            await server.run(streams[0], streams[1], server.create_initialization_options())
    elif path.startswith("/messages/"):
        await sse_transport.handle_post_message(scope, receive, send)
    else:
        response = JSONResponse({"error": "not found"}, status_code=404)
        await response(scope, receive, send)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
