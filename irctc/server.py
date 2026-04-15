"""
Indian Railways (IRCTC) MCP Server — remote HTTP/SSE endpoint for claude.ai
Endpoint: GET /sse

Data sources (no API key required by default):
- Primary: RapidAPI IRCTC API (set RAPIDAPI_KEY for richer data)
- Fallback: Publicly accessible train info endpoints

Tools:
  check_pnr_status(pnr)
  search_trains(from_station, to_station, date)
  get_train_schedule(train_number)
  search_stations(query)
"""
import os
import json
import httpx
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.types import Tool, TextContent
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route, Mount
import uvicorn

PORT = int(os.environ.get("PORT", 8000))
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY", "")

RAPIDAPI_HOST = "irctc1.p.rapidapi.com"
RAPIDAPI_HEADERS = {
    "x-rapidapi-host": RAPIDAPI_HOST,
    "x-rapidapi-key": RAPIDAPI_KEY,
}

WEB_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-IN,en;q=0.9",
}

server = Server("irctc-mcp")
sse_transport = SseServerTransport("/messages/")


# ── Tool registry ─────────────────────────────────────────────────────────────

@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="check_pnr_status",
            description="Check the current PNR booking status for an Indian Railways ticket. Returns passenger status, coach, berth, and journey details.",
            inputSchema={
                "type": "object",
                "properties": {
                    "pnr": {"type": "string", "description": "10-digit PNR number printed on your ticket."}
                },
                "required": ["pnr"]
            }
        ),
        Tool(
            name="search_trains",
            description=(
                "Search for trains between two Indian railway stations on a given date. "
                "Use standard station codes: NDLS (New Delhi), BCT (Mumbai Central), "
                "MAS (Chennai Central), HWH (Howrah), CSTM (Mumbai CST), SBC (Bengaluru)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "from_station": {"type": "string", "description": "Source station code, e.g. 'NDLS'"},
                    "to_station": {"type": "string", "description": "Destination station code, e.g. 'BCT'"},
                    "date": {"type": "string", "description": "Date of journey in YYYYMMDD format, e.g. '20250415'"}
                },
                "required": ["from_station", "to_station", "date"]
            }
        ),
        Tool(
            name="get_train_schedule",
            description="Get the full station-by-station schedule/timetable for any Indian train number.",
            inputSchema={
                "type": "object",
                "properties": {
                    "train_number": {"type": "string", "description": "5-digit train number, e.g. '12301' (Howrah Rajdhani)"}
                },
                "required": ["train_number"]
            }
        ),
        Tool(
            name="search_stations",
            description="Find Indian railway station codes by name or partial name.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Station name or partial name, e.g. 'Mumbai', 'Delhi', 'Bengaluru'"}
                },
                "required": ["query"]
            }
        )
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "check_pnr_status":
        result = await _check_pnr(arguments["pnr"])
    elif name == "search_trains":
        result = await _search_trains(
            arguments["from_station"], arguments["to_station"], arguments["date"]
        )
    elif name == "get_train_schedule":
        result = await _get_schedule(arguments["train_number"])
    elif name == "search_stations":
        result = await _search_stations(arguments["query"])
    else:
        raise ValueError(f"Unknown tool: {name}")
    return [TextContent(type="text", text=result)]


# ── Implementations ───────────────────────────────────────────────────────────

async def _check_pnr(pnr: str) -> str:
    pnr = pnr.strip().replace(" ", "")
    if len(pnr) != 10 or not pnr.isdigit():
        return "❌ Invalid PNR. Please provide a 10-digit PNR number (found on your ticket)."

    if RAPIDAPI_KEY:
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                r = await client.get(
                    f"https://{RAPIDAPI_HOST}/api/v3/getPNRStatus",
                    params={"pnrNumber": pnr},
                    headers=RAPIDAPI_HEADERS
                )
                if r.status_code == 200:
                    data = r.json()
                    return _format_pnr(pnr, data)
        except Exception as e:
            pass  # Fall through to message

    # No API key or request failed — provide instructions
    return (
        f"🚂 **PNR Status Check for {pnr}**\n\n"
        "To get live PNR status, add a RAPIDAPI_KEY to this service:\n"
        "1. Sign up free at https://rapidapi.com\n"
        "2. Subscribe to 'IRCTC API' by Apiwiz (free tier: 500 calls/month)\n"
        "   URL: https://rapidapi.com/Adeel_25/api/irctc1\n"
        "3. Copy your RapidAPI key → Railway → irctc service → Variables → RAPIDAPI_KEY\n\n"
        f"Direct check: https://www.indianrail.gov.in/enquiry/PNR/PnrEnquiry.html"
    )


def _format_pnr(pnr: str, data: dict) -> str:
    if not data.get("status"):
        return f"Could not fetch PNR {pnr}: {data.get('message', 'Unknown error')}"

    b = data.get("body", {})
    lines = [
        f"🎫 **PNR: {b.get('pnrNumber', pnr)}**",
        f"🚂 Train: {b.get('trainNumber', '')} — {b.get('trainName', '')}",
        f"📅 Date: {b.get('dateOfJourney', 'N/A')}",
        f"🛤️  Route: {b.get('boardingStation', '')} → {b.get('reservationUpto', '')}",
        f"💺 Class: {b.get('classOfTravel', 'N/A')}",
        f"📋 Chart: {b.get('chartStatus', 'Not prepared')}",
        "",
        "**Passenger Status:**"
    ]
    for i, p in enumerate(b.get("passengerList", []), 1):
        status = p.get("currentStatusDetails", "N/A")
        coach = p.get("currentCoachId", "—")
        berth = p.get("currentBerthNo", "—")
        lines.append(f"  {i}. {status} | Coach: {coach} | Berth: {berth}")

    return "\n".join(lines)


async def _search_trains(from_stn: str, to_stn: str, date: str) -> str:
    from_stn = from_stn.strip().upper()
    to_stn = to_stn.strip().upper()

    if RAPIDAPI_KEY:
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                r = await client.get(
                    f"https://{RAPIDAPI_HOST}/api/v3/trainBetweenStations",
                    params={
                        "fromStationCode": from_stn,
                        "toStationCode": to_stn,
                        "dateOfJourney": date
                    },
                    headers=RAPIDAPI_HEADERS
                )
                if r.status_code == 200:
                    data = r.json()
                    return _format_trains(from_stn, to_stn, date, data)
        except Exception:
            pass

    return (
        f"🚂 **Trains from {from_stn} → {to_stn} on {date}**\n\n"
        "To get live train data, add RAPIDAPI_KEY (free tier available):\n"
        "1. https://rapidapi.com → subscribe to 'IRCTC API' by Apiwiz\n"
        "2. Set RAPIDAPI_KEY in Railway → irctc service → Variables\n\n"
        f"Search now: https://www.irctc.co.in/nget/train-search\n"
        f"Or: https://erail.in/trains/{from_stn}/{to_stn}/{date}"
    )


def _format_trains(from_stn: str, to_stn: str, date: str, data: dict) -> str:
    if not data.get("status"):
        return f"Error: {data.get('message', 'No trains found')}"

    trains = data.get("body", [])
    if not trains:
        return f"No trains found from {from_stn} to {to_stn} on {date}."

    lines = [f"🚂 **{len(trains)} trains found: {from_stn} → {to_stn} on {date}**\n"]
    for t in trains[:20]:
        avail = t.get("avlDayList", [])
        avail_str = ", ".join(avail) if avail else ""
        lines.append(
            f"**{t.get('trainNumber', '')} — {t.get('trainName', '')}**\n"
            f"  Departs: {t.get('departureTime', '')} | Arrives: {t.get('arrivalTime', '')} | "
            f"Duration: {t.get('duration', '')}\n"
            f"  Runs on: {avail_str or 'Check IRCTC'}\n"
        )
    return "\n".join(lines)


async def _get_schedule(train_no: str) -> str:
    train_no = train_no.strip()

    if RAPIDAPI_KEY:
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                r = await client.get(
                    f"https://{RAPIDAPI_HOST}/api/v1/getTrainSchedule",
                    params={"trainNo": train_no},
                    headers=RAPIDAPI_HEADERS
                )
                if r.status_code == 200:
                    data = r.json()
                    body = data.get("body", [])
                    if body:
                        lines = [f"🚂 **Schedule — Train {train_no}:**\n"]
                        for stop in body:
                            lines.append(
                                f"  {stop.get('stationSerialNumber', ''):>3}. "
                                f"{stop.get('stnCode', ''):>5} — {stop.get('stnName', ''):<30} "
                                f"Arr: {stop.get('schArrTime', '--'):>5} | "
                                f"Dep: {stop.get('schDeptTime', '--'):>5} | "
                                f"Day {stop.get('dayCount', 1)}"
                            )
                        return "\n".join(lines)
        except Exception:
            pass

    return (
        f"🚂 **Train {train_no} Schedule**\n\n"
        "Add RAPIDAPI_KEY to Railway → irctc service → Variables for live schedule.\n\n"
        f"View schedule: https://erail.in/train/{train_no}"
    )


async def _search_stations(query: str) -> str:
    if RAPIDAPI_KEY:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.get(
                    f"https://{RAPIDAPI_HOST}/api/v1/searchStation",
                    params={"query": query},
                    headers=RAPIDAPI_HEADERS
                )
                if r.status_code == 200:
                    data = r.json()
                    stations = data.get("body", [])
                    if stations:
                        lines = [f"🔍 **Stations matching '{query}':**"]
                        for s in stations[:15]:
                            lines.append(f"  {s.get('stationCode', ''):>6} — {s.get('stationName', '')}")
                        return "\n".join(lines)
                    return f"No stations found for '{query}'"
        except Exception:
            pass

    # Common stations hardcoded as fallback
    common = {
        "delhi": "NDLS (New Delhi), DLI (Old Delhi), NZM (Hazrat Nizamuddin), DEE (Delhi Sarai Rohilla)",
        "mumbai": "BCT (Mumbai Central), CSTM (Chhatrapati Shivaji Terminus), LTT (Lokmanya Tilak Terminus), DR (Dadar)",
        "chennai": "MAS (Chennai Central), MS (Chennai Egmore), TBM (Tambaram)",
        "kolkata": "HWH (Howrah), KOAA (Kolkata), SDAH (Sealdah)",
        "bangalore": "SBC (Krantivira Sangolli Rayanna / Bengaluru City), YPR (Yeshvantpur), BNC (Bengaluru Cantt)",
        "hyderabad": "SC (Secunderabad), HYB (Hyderabad Deccan), KCG (Kacheguda)",
        "ahmedabad": "ADI (Ahmedabad), ADI (Gandhi Gram)",
        "pune": "PUNE (Pune Jn), PNVL (Panvel)",
        "jaipur": "JP (Jaipur), JIPA (Jaipur Metro)",
        "lucknow": "LKO (Lucknow), LJN (Lucknow Junction NER)",
    }
    for key, val in common.items():
        if query.lower() in key:
            return f"🔍 Common station codes for {query.title()}:\n  {val}\n\nAdd RAPIDAPI_KEY for full search."

    return (
        f"Station search for '{query}':\n"
        "Add RAPIDAPI_KEY (free) for live station search.\n"
        f"Manual search: https://www.irctc.co.in/nget/train-search"
    )


# ── HTTP app ──────────────────────────────────────────────────────────────────

async def handle_sse(request: Request):
    async with sse_transport.connect_sse(
        request.scope, request.receive, request._send
    ) as streams:
        await server.run(streams[0], streams[1], server.create_initialization_options())
    return Response()


async def health(request: Request):
    return JSONResponse({"status": "ok", "service": "irctc-mcp"})


app = Starlette(
    routes=[
        Route("/health", health),
        Route("/sse", handle_sse),
        Mount("/messages/", app=sse_transport.handle_post_message),
    ]
)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
