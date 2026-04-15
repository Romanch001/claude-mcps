"""
Airbnb MCP Server — remote HTTP/SSE endpoint for claude.ai
Provides travel accommodation search and info using public data sources.
No API key required.
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
server = Server("airbnb-mcp")
sse_transport = SseServerTransport("/messages/")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/json",
    "X-Airbnb-API-Key": "d306zoyjsyarp7ifhu67rjxn52tv0t20",  # public key used in browser
}


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="search_listings",
            description="Search Airbnb listings in a city for given dates and guest count.",
            inputSchema={
                "type": "object",
                "properties": {
                    "location": {"type": "string", "description": "City or area to search, e.g. 'Mumbai, India', 'Goa, India', 'Paris, France'."},
                    "checkin": {"type": "string", "description": "Check-in date YYYY-MM-DD (optional)."},
                    "checkout": {"type": "string", "description": "Check-out date YYYY-MM-DD (optional)."},
                    "adults": {"type": "integer", "description": "Number of adult guests. Default: 2.", "default": 2},
                    "price_max": {"type": "integer", "description": "Maximum price per night in USD (optional)."},
                    "room_type": {"type": "string", "description": "Filter: 'entire_home', 'private_room', 'shared_room'. Leave empty for all."}
                },
                "required": ["location"]
            }
        ),
        Tool(
            name="get_city_travel_info",
            description="Get travel and accommodation overview for a city: typical prices, best neighborhoods, tips.",
            inputSchema={
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "City name, e.g. 'Mumbai', 'Goa', 'Jaipur', 'Bangalore', 'Paris', 'Tokyo'."}
                },
                "required": ["city"]
            }
        ),
        Tool(
            name="estimate_trip_cost",
            description="Estimate total accommodation cost for a trip based on city and duration.",
            inputSchema={
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "Destination city."},
                    "nights": {"type": "integer", "description": "Number of nights."},
                    "guests": {"type": "integer", "description": "Number of guests. Default: 2.", "default": 2},
                    "accommodation_type": {"type": "string", "description": "Type: 'budget', 'mid-range', 'luxury'. Default: mid-range.", "default": "mid-range"}
                },
                "required": ["city", "nights"]
            }
        ),
        Tool(
            name="get_neighborhood_guide",
            description="Get a neighborhood guide for a city — best areas to stay, what each area is known for.",
            inputSchema={
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "City to get neighborhood guide for."}
                },
                "required": ["city"]
            }
        )
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "search_listings":
        result = await _search_listings(arguments)
    elif name == "get_city_travel_info":
        result = _city_info(arguments.get("city", ""))
    elif name == "estimate_trip_cost":
        result = _estimate_cost(arguments)
    elif name == "get_neighborhood_guide":
        result = _neighborhood_guide(arguments.get("city", ""))
    else:
        raise ValueError(f"Unknown tool: {name}")
    return [TextContent(type="text", text=result)]


async def _search_listings(args: dict) -> str:
    location = args.get("location", "")
    checkin = args.get("checkin", "")
    checkout = args.get("checkout", "")
    adults = args.get("adults", 2)
    price_max = args.get("price_max")
    room_type = args.get("room_type", "")

    params = {
        "operationName": "ExploreSearch",
        "locale": "en",
        "currency": "USD",
        "query": location,
        "adults": adults,
        "_format": "for_explore_search_web",
        "items_per_grid": 10,
        "refinement_paths[]": "/homes",
        "search_type": "AUTOSUGGEST",
    }
    if checkin:
        params["checkin"] = checkin
    if checkout:
        params["checkout"] = checkout
    if price_max:
        params["price_max"] = price_max
    if room_type:
        room_map = {"entire_home": "Entire home/apt", "private_room": "Private room", "shared_room": "Shared room"}
        params["room_types[]"] = room_map.get(room_type, room_type)

    try:
        async with httpx.AsyncClient(timeout=20, headers=HEADERS) as client:
            r = await client.get(
                "https://www.airbnb.com/api/v2/explore_tabs",
                params=params
            )

        if r.status_code == 200:
            data = r.json()
            sections = data.get("explore_tabs", [{}])[0].get("sections", [])
            listings = []
            for section in sections:
                for item in section.get("listings", []):
                    l = item.get("listing", {})
                    p = item.get("pricing_quote", {})
                    listings.append({
                        "name": l.get("name", ""),
                        "room_type": l.get("room_type_category", ""),
                        "rating": l.get("avg_rating", ""),
                        "reviews": l.get("reviews_count", 0),
                        "price_night": p.get("rate", {}).get("amount", "?"),
                        "id": l.get("id", ""),
                    })

            if listings:
                lines = [f"**Airbnb listings in {location}:**\n"]
                for li in listings[:10]:
                    lines.append(
                        f"• **{li['name']}** ({li['room_type']})\n"
                        f"  ⭐{li['rating']} ({li['reviews']} reviews) | ${li['price_night']}/night"
                    )
                return "\n".join(lines)
    except Exception:
        pass

    # Fallback: curated info
    return _city_search_fallback(location, checkin, checkout, adults)


def _city_search_fallback(location: str, checkin: str, checkout: str, adults: int) -> str:
    loc_lower = location.lower()
    prices = _get_city_prices(loc_lower)
    return (
        f"**Airbnb Search: {location}**\n"
        f"Guests: {adults} | Check-in: {checkin or 'flexible'} | Check-out: {checkout or 'flexible'}\n\n"
        f"**Typical price ranges for {location}:**\n"
        f"  Budget (shared/private room): {prices['budget']}\n"
        f"  Mid-range (entire apartment): {prices['mid']}\n"
        f"  Luxury (premium home/villa):  {prices['luxury']}\n\n"
        f"**Search directly:** https://www.airbnb.com/s/{location.replace(' ', '-')}/homes"
        f"{'?checkin=' + checkin if checkin else ''}"
        f"{'&checkout=' + checkout if checkout else ''}"
        f"&adults={adults}"
    )


def _get_city_prices(city: str) -> dict:
    price_db = {
        "goa": {"budget": "$15–30/night", "mid": "$40–80/night", "luxury": "$100–300/night"},
        "mumbai": {"budget": "$20–40/night", "mid": "$50–100/night", "luxury": "$120–400/night"},
        "delhi": {"budget": "$15–35/night", "mid": "$45–90/night", "luxury": "$100–350/night"},
        "jaipur": {"budget": "$10–25/night", "mid": "$30–70/night", "luxury": "$80–250/night"},
        "bangalore": {"budget": "$18–35/night", "mid": "$45–90/night", "luxury": "$100–300/night"},
        "paris": {"budget": "$50–80/night", "mid": "$100–200/night", "luxury": "$250–800/night"},
        "london": {"budget": "$60–100/night", "mid": "$120–250/night", "luxury": "$300–1000/night"},
        "tokyo": {"budget": "$40–70/night", "mid": "$80–180/night", "luxury": "$200–600/night"},
        "new york": {"budget": "$80–120/night", "mid": "$150–300/night", "luxury": "$400–1500/night"},
        "bali": {"budget": "$20–40/night", "mid": "$50–120/night", "luxury": "$150–500/night"},
    }
    for key, prices in price_db.items():
        if key in city:
            return prices
    return {"budget": "$20–50/night", "mid": "$60–150/night", "luxury": "$200–500/night"}


def _city_info(city: str) -> str:
    city_lower = city.lower()
    info_db = {
        "goa": {
            "overview": "India's beach paradise — lush coastline, Portuguese heritage, vibrant nightlife.",
            "best_areas": "North Goa (Baga, Calangute, Anjuna) for parties; South Goa (Palolem, Agonda) for tranquil beaches.",
            "best_time": "November to February (cool and dry)",
            "avg_price": "$40–80/night (entire apartment)",
            "tips": "Book 3+ months ahead for December/New Year. Scooter rental (~₹300/day) is the best way to get around.",
        },
        "mumbai": {
            "overview": "India's financial capital — Bollywood, street food, colonial architecture.",
            "best_areas": "Bandra (trendy, cafes), Colaba (tourist hub, Gateway of India), Juhu (beachside).",
            "best_time": "November to February",
            "avg_price": "$50–100/night",
            "tips": "Avoid monsoon (June–Sept). Local trains are the fastest transport.",
        },
        "jaipur": {
            "overview": "The Pink City — forts, palaces, bazaars. Great for heritage tourism.",
            "best_areas": "Old City (near Hawa Mahal), C-Scheme (upscale), Bani Park.",
            "best_time": "October to March",
            "avg_price": "$30–70/night",
            "tips": "Combine with Agra and Delhi for the Golden Triangle circuit.",
        },
        "paris": {
            "overview": "The City of Light — museums, cuisine, romance.",
            "best_areas": "Le Marais (trendy, central), Montmartre (artistic), Saint-Germain (classic), Bastille (local).",
            "best_time": "April–June, September–October",
            "avg_price": "$100–200/night",
            "tips": "Metro pass saves money. Book Eiffel Tower tickets weeks in advance.",
        },
        "bali": {
            "overview": "Indonesian island paradise — temples, terraced rice fields, surf, yoga retreats.",
            "best_areas": "Seminyak (upscale), Canggu (digital nomads, surf), Ubud (culture, jungle), Uluwatu (cliff views).",
            "best_time": "April–October (dry season)",
            "avg_price": "$50–120/night",
            "tips": "Rent a scooter or hire a private driver. Respect temple dress codes.",
        },
    }
    for key, info in info_db.items():
        if key in city_lower:
            return (
                f"**{city} Travel & Accommodation Guide**\n\n"
                f"Overview: {info['overview']}\n\n"
                f"Best areas to stay: {info['best_areas']}\n\n"
                f"Best time to visit: {info['best_time']}\n"
                f"Average Airbnb price: {info['avg_price']}\n\n"
                f"Tips: {info['tips']}\n\n"
                f"Search Airbnb: https://www.airbnb.com/s/{city.replace(' ', '-')}/homes"
            )
    return (
        f"**{city} Accommodation Overview**\n\n"
        f"Search Airbnb listings: https://www.airbnb.com/s/{city.replace(' ', '-')}/homes\n\n"
        f"General tips:\n"
        f"• Compare prices across Airbnb, Booking.com, and MakeMyTrip\n"
        f"• Read reviews carefully, especially for cleanliness and location\n"
        f"• Message hosts before booking to confirm amenities\n"
        f"• Book refundable options when plans are uncertain"
    )


def _estimate_cost(args: dict) -> str:
    city = args.get("city", "")
    nights = int(args.get("nights", 1))
    guests = int(args.get("guests", 2))
    acc_type = args.get("accommodation_type", "mid-range")

    prices = _get_city_prices(city.lower())
    type_map = {"budget": "budget", "mid-range": "mid", "luxury": "luxury"}
    key = type_map.get(acc_type, "mid")
    price_range = prices[key]

    # Parse approximate midpoint
    try:
        parts = price_range.replace("$", "").replace("/night", "").split("–")
        low = float(parts[0])
        high = float(parts[1])
        mid = (low + high) / 2
    except Exception:
        low, mid, high = 50, 75, 100

    low_total = low * nights
    mid_total = mid * nights
    high_total = high * nights

    service_fee_pct = 0.14
    taxes_pct = 0.10

    return (
        f"**Trip Cost Estimate: {city}**\n"
        f"Duration: {nights} nights | Guests: {guests} | Type: {acc_type}\n\n"
        f"Nightly rate: {price_range}\n\n"
        f"**Total accommodation cost:**\n"
        f"  Low end:  ${low_total:.0f} + ~${low_total*service_fee_pct:.0f} service fee + ~${low_total*taxes_pct:.0f} taxes = **${low_total*(1+service_fee_pct+taxes_pct):.0f}**\n"
        f"  Midpoint: ${mid_total:.0f} + ~${mid_total*service_fee_pct:.0f} service fee + ~${mid_total*taxes_pct:.0f} taxes = **${mid_total*(1+service_fee_pct+taxes_pct):.0f}**\n"
        f"  High end: ${high_total:.0f} + ~${high_total*service_fee_pct:.0f} service fee + ~${high_total*taxes_pct:.0f} taxes = **${high_total*(1+service_fee_pct+taxes_pct):.0f}**\n\n"
        f"*Airbnb typically adds 14% service fee + local taxes.*"
    )


def _neighborhood_guide(city: str) -> str:
    guides = {
        "mumbai": """\
**Mumbai Neighborhood Guide:**
• **Colaba** — Tourist hub, Gateway of India, Leopold Cafe, budget guesthouses. Best for first-timers.
• **Bandra** — Trendy cafes, nightlife, Bandstand promenade, BKC nearby. Best for millennials/business.
• **Juhu** — Beach, Bollywood star homes, upscale hotels. Best for families.
• **Andheri** — Near airport, shopping malls, suburban vibe. Best for transit convenience.
• **Lower Parel** — Corporate area, high-rises, Phoenix Mall. Best for business travellers.
• **Dadar** — Local, authentic Mumbai, good food. Best for budget travellers wanting local life.
""",
        "delhi": """\
**Delhi Neighborhood Guide:**
• **Connaught Place** — Central, metro accessible, shopping, restaurants. Business-friendly.
• **Paharganj** — Budget backpacker area near New Delhi Railway Station. Cheap but chaotic.
• **Hauz Khas** — Trendy, cafes, nightlife, ruins. Best for young travellers.
• **Karol Bagh** — Shopping, local markets, mid-range hotels. Great for shopping trips.
• **South Delhi (Defence Colony/GK)** — Upscale, quiet, good restaurants. Best for families.
• **Old Delhi (Chandni Chowk)** — Heritage, street food, madness. Best for food/history lovers.
""",
        "goa": """\
**Goa Neighborhood Guide:**
• **Baga/Calangute** — Most popular, beach shacks, water sports, budget to mid-range.
• **Anjuna** — Flea market, rave culture, hippie vibe. Best for backpackers.
• **Vagator** — Cliffs, less crowded, beautiful. Good balance of lively and peaceful.
• **Palolem (South)** — Crescent beach, serene, families. Best for relaxation.
• **Agonda (South)** — Quiet, pristine, eco-friendly resorts. Best for couples.
• **Candolim** — Quieter than Baga, upscale resorts, family-friendly.
""",
    }
    city_lower = city.lower()
    for key, guide in guides.items():
        if key in city_lower:
            return guide
    return (
        f"**{city} Neighborhood Guide**\n\n"
        f"For a detailed neighborhood guide for {city}, check:\n"
        f"• Airbnb's neighborhood guide: https://www.airbnb.com/s/{city.replace(' ','-')}/homes\n"
        f"• Lonely Planet: https://www.lonelyplanet.com/search?q={city.replace(' ','+')}+neighborhoods\n"
        f"• TripAdvisor: https://www.tripadvisor.com/Search?q={city.replace(' ','+')}+best+area+to+stay"
    )


async def handle_sse(request: Request):
    async with sse_transport.connect_sse(
        request.scope, request.receive, request._send
    ) as streams:
        await server.run(streams[0], streams[1], server.create_initialization_options())


async def health(request: Request):
    return JSONResponse({"status": "ok", "service": "airbnb-mcp"})


app = Starlette(
    routes=[
        Route("/health", health),
        Route("/sse", handle_sse),
        Route("/messages/", sse_transport.handle_post_message),
    ]
)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
