"""
MyFitnessPal / Nutrition MCP Server — remote HTTP/SSE endpoint for claude.ai
Uses Open Food Facts API (completely free, no API key) and USDA FoodData Central
for comprehensive nutrition data.
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
server = Server("myfitnesspal-mcp")
sse_transport = SseServerTransport("/messages/")

OFF_BASE = "https://world.openfoodfacts.org"
USDA_BASE = "https://api.nal.usda.gov/fdc/v1"
USDA_KEY = os.environ.get("USDA_API_KEY", "DEMO_KEY")  # DEMO_KEY works with rate limits


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="search_food",
            description=(
                "Search for food items and get detailed nutritional information. "
                "Uses Open Food Facts database (6M+ products worldwide)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Food name to search, e.g. 'apple', 'chicken breast', 'Maggi noodles', 'Amul butter'."},
                    "limit": {"type": "integer", "description": "Number of results (1-10). Default: 5.", "default": 5}
                },
                "required": ["query"]
            }
        ),
        Tool(
            name="get_nutrition_by_barcode",
            description="Get full nutritional info for a product by its barcode (EAN/UPC).",
            inputSchema={
                "type": "object",
                "properties": {
                    "barcode": {"type": "string", "description": "Product barcode number (EAN-13 or UPC-A), e.g. '8901058851336' for Maggi 2-Minute Noodles."}
                },
                "required": ["barcode"]
            }
        ),
        Tool(
            name="calculate_meal_nutrition",
            description="Calculate total nutrition for a meal given food items and quantities.",
            inputSchema={
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "description": "List of food items with quantities.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "food": {"type": "string", "description": "Food name."},
                                "quantity_g": {"type": "number", "description": "Quantity in grams."}
                            },
                            "required": ["food", "quantity_g"]
                        }
                    }
                },
                "required": ["items"]
            }
        ),
        Tool(
            name="get_daily_recommendations",
            description="Get daily nutrition recommendations (RDAs) based on age, gender, and activity level.",
            inputSchema={
                "type": "object",
                "properties": {
                    "age": {"type": "integer", "description": "Age in years."},
                    "gender": {"type": "string", "description": "'male' or 'female'."},
                    "activity_level": {"type": "string", "description": "'sedentary', 'light', 'moderate', 'active', 'very_active'. Default: moderate.", "default": "moderate"},
                    "weight_kg": {"type": "number", "description": "Body weight in kg (optional, for precise calorie calc)."}
                },
                "required": ["age", "gender"]
            }
        ),
        Tool(
            name="search_usda_food",
            description="Search USDA FoodData Central for raw/generic foods with detailed nutrient breakdown.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Food to search, e.g. 'raw chicken breast', 'brown rice cooked', 'banana'."},
                    "limit": {"type": "integer", "description": "Number of results (1-10). Default: 5.", "default": 5}
                },
                "required": ["query"]
            }
        )
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    async with httpx.AsyncClient(timeout=20) as client:
        if name == "search_food":
            result = await _search_food(client, arguments)
        elif name == "get_nutrition_by_barcode":
            result = await _barcode(client, arguments)
        elif name == "calculate_meal_nutrition":
            result = await _calc_meal(client, arguments)
        elif name == "get_daily_recommendations":
            result = _daily_recommendations(arguments)
        elif name == "search_usda_food":
            result = await _search_usda(client, arguments)
        else:
            raise ValueError(f"Unknown tool: {name}")
    return [TextContent(type="text", text=result)]


def _extract_nutrients(product: dict) -> dict:
    n = product.get("nutriments", {})
    return {
        "calories": n.get("energy-kcal_100g", n.get("energy_100g", 0)),
        "protein_g": n.get("proteins_100g", 0),
        "carbs_g": n.get("carbohydrates_100g", 0),
        "fat_g": n.get("fat_100g", 0),
        "fiber_g": n.get("fiber_100g", 0),
        "sugar_g": n.get("sugars_100g", 0),
        "sodium_mg": n.get("sodium_100g", 0) * 1000 if n.get("sodium_100g") else n.get("salt_100g", 0) * 400,
    }


async def _search_food(client, args) -> str:
    q = args.get("query", "")
    limit = min(int(args.get("limit", 5)), 10)
    r = await client.get(
        f"{OFF_BASE}/cgi/search.pl",
        params={"search_terms": q, "search_simple": 1, "action": "process", "json": 1, "page_size": limit}
    )
    if r.status_code != 200:
        return f"Search error: HTTP {r.status_code}"
    products = r.json().get("products", [])
    if not products:
        return f"No food found for '{q}'. Try a more specific name."

    lines = [f"**Nutrition info for '{q}'** (per 100g):\n"]
    for p in products[:limit]:
        name = p.get("product_name", "Unknown")
        brand = p.get("brands", "")
        n = _extract_nutrients(p)
        if not n["calories"]:
            continue
        lines.append(
            f"• **{name}**{' — ' + brand if brand else ''}\n"
            f"  Calories: {n['calories']:.0f} kcal | Protein: {n['protein_g']:.1f}g | "
            f"Carbs: {n['carbs_g']:.1f}g | Fat: {n['fat_g']:.1f}g | "
            f"Fiber: {n['fiber_g']:.1f}g"
        )
    return "\n".join(lines) if len(lines) > 1 else f"No detailed nutrition data found for '{q}'."


async def _barcode(client, args) -> str:
    barcode = args.get("barcode", "").strip()
    r = await client.get(f"{OFF_BASE}/api/v0/product/{barcode}.json")
    if r.status_code != 200:
        return f"Error {r.status_code}"
    data = r.json()
    if data.get("status") != 1:
        return f"Product with barcode '{barcode}' not found in Open Food Facts."
    p = data["product"]
    name = p.get("product_name", "Unknown product")
    brand = p.get("brands", "")
    n = _extract_nutrients(p)
    serving = p.get("serving_size", "100g")
    allergens = p.get("allergens_tags", [])
    nutriscore = p.get("nutriscore_grade", "?").upper()

    lines = [
        f"**{name}**{' — ' + brand if brand else ''}",
        f"Barcode: {barcode} | Nutri-Score: {nutriscore}",
        f"Serving size: {serving}",
        f"\n**Nutrition (per 100g):**",
        f"  Calories: {n['calories']:.0f} kcal",
        f"  Protein: {n['protein_g']:.1f} g",
        f"  Carbohydrates: {n['carbs_g']:.1f} g (sugars: {n['sugar_g']:.1f} g)",
        f"  Fat: {n['fat_g']:.1f} g",
        f"  Fiber: {n['fiber_g']:.1f} g",
        f"  Sodium: {n['sodium_mg']:.0f} mg",
    ]
    if allergens:
        allergen_str = ", ".join(a.replace("en:", "").replace("-", " ") for a in allergens)
        lines.append(f"\n⚠️ Allergens: {allergen_str}")
    return "\n".join(lines)


async def _calc_meal(client, args) -> str:
    items = args.get("items", [])
    if not items:
        return "No food items provided."

    totals = {"calories": 0, "protein_g": 0, "carbs_g": 0, "fat_g": 0, "fiber_g": 0}
    lines = ["**Meal Nutrition Calculation:**\n"]

    for item in items:
        food = item.get("food", "")
        qty_g = float(item.get("quantity_g", 100))
        r = await client.get(
            f"{OFF_BASE}/cgi/search.pl",
            params={"search_terms": food, "search_simple": 1, "action": "process", "json": 1, "page_size": 1}
        )
        products = r.json().get("products", []) if r.status_code == 200 else []
        if products and products[0].get("nutriments", {}).get("energy-kcal_100g"):
            p = products[0]
            n = _extract_nutrients(p)
            factor = qty_g / 100
            item_cals = n["calories"] * factor
            item_prot = n["protein_g"] * factor
            item_carbs = n["carbs_g"] * factor
            item_fat = n["fat_g"] * factor
            item_fiber = n["fiber_g"] * factor
            totals["calories"] += item_cals
            totals["protein_g"] += item_prot
            totals["carbs_g"] += item_carbs
            totals["fat_g"] += item_fat
            totals["fiber_g"] += item_fiber
            lines.append(
                f"• **{food}** ({qty_g:.0f}g): {item_cals:.0f} kcal | "
                f"P:{item_prot:.1f}g C:{item_carbs:.1f}g F:{item_fat:.1f}g"
            )
        else:
            lines.append(f"• **{food}** ({qty_g:.0f}g): ⚠️ Nutrition data not found")

    lines.append(
        f"\n**TOTAL MEAL:**\n"
        f"  Calories: {totals['calories']:.0f} kcal\n"
        f"  Protein: {totals['protein_g']:.1f} g\n"
        f"  Carbohydrates: {totals['carbs_g']:.1f} g\n"
        f"  Fat: {totals['fat_g']:.1f} g\n"
        f"  Fiber: {totals['fiber_g']:.1f} g"
    )
    return "\n".join(lines)


def _daily_recommendations(args) -> str:
    age = int(args.get("age", 25))
    gender = args.get("gender", "male").lower()
    activity = args.get("activity_level", "moderate")
    weight = float(args.get("weight_kg", 70 if gender == "male" else 60))

    # Harris-Benedict BMR
    if gender == "male":
        bmr = 88.362 + (13.397 * weight) + (4.799 * 170) - (5.677 * age)
    else:
        bmr = 447.593 + (9.247 * weight) + (3.098 * 163) - (4.330 * age)

    multipliers = {"sedentary": 1.2, "light": 1.375, "moderate": 1.55, "active": 1.725, "very_active": 1.9}
    tdee = bmr * multipliers.get(activity, 1.55)
    protein_g = weight * 0.8  # RDA
    fat_g = tdee * 0.25 / 9
    carbs_g = (tdee - protein_g * 4 - fat_g * 9) / 4

    return (
        f"**Daily Nutrition Recommendations**\n"
        f"Profile: {gender.title()}, {age} years, {weight} kg, {activity} activity\n\n"
        f"BMR: {bmr:.0f} kcal/day\n"
        f"TDEE (maintenance): **{tdee:.0f} kcal/day**\n"
        f"  For weight loss:  {tdee-500:.0f} kcal/day (-500)\n"
        f"  For weight gain:  {tdee+300:.0f} kcal/day (+300)\n\n"
        f"**Macronutrients:**\n"
        f"  Protein: {protein_g:.0f}g ({protein_g*4:.0f} kcal) — 0.8g/kg body weight\n"
        f"  Fat:     {fat_g:.0f}g ({fat_g*9:.0f} kcal) — 25% of TDEE\n"
        f"  Carbs:   {carbs_g:.0f}g ({carbs_g*4:.0f} kcal) — remainder\n\n"
        f"**Key Micronutrients (RDA):**\n"
        f"  Vitamin D: 600 IU | Calcium: 1000 mg | Iron: {'8' if gender=='male' else '18'} mg\n"
        f"  Sodium: <2300 mg | Fiber: {'38' if gender=='male' else '25'} g | Water: {'3.7' if gender=='male' else '2.7'}L"
    )


async def _search_usda(client, args) -> str:
    q = args.get("query", "")
    limit = min(int(args.get("limit", 5)), 10)
    r = await client.get(
        f"{USDA_BASE}/foods/search",
        params={"query": q, "pageSize": limit, "api_key": USDA_KEY, "dataType": "Foundation,SR Legacy"}
    )
    if r.status_code != 200:
        return f"USDA search error: HTTP {r.status_code}"
    foods = r.json().get("foods", [])
    if not foods:
        return f"No USDA data found for '{q}'."
    lines = [f"**USDA FoodData: '{q}'** (per 100g):\n"]
    for f in foods[:limit]:
        nutrients = {n["nutrientName"]: n["value"] for n in f.get("foodNutrients", [])}
        kcal = nutrients.get("Energy", 0)
        prot = nutrients.get("Protein", 0)
        carbs = nutrients.get("Carbohydrate, by difference", 0)
        fat = nutrients.get("Total lipid (fat)", 0)
        fiber = nutrients.get("Fiber, total dietary", 0)
        lines.append(
            f"• **{f.get('description','')}** (FDC ID: {f.get('fdcId','')})\n"
            f"  {kcal:.0f} kcal | Protein: {prot:.1f}g | Carbs: {carbs:.1f}g | Fat: {fat:.1f}g | Fiber: {fiber:.1f}g"
        )
    return "\n".join(lines)


async def handle_sse(request: Request):
    async with sse_transport.connect_sse(
        request.scope, request.receive, request._send
    ) as streams:
        await server.run(streams[0], streams[1], server.create_initialization_options())


async def health(request: Request):
    return JSONResponse({"status": "ok", "service": "myfitnesspal-mcp"})


app = Starlette(
    routes=[
        Route("/health", health),
        Route("/sse", handle_sse),
        Route("/messages/", sse_transport.handle_post_message),
    ]
)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
