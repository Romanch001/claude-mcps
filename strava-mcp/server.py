"""
Strava MCP Server — remote HTTP/SSE endpoint for claude.ai
Uses Strava API v3.

Required env vars:
  STRAVA_CLIENT_ID     — from strava.com/settings/api
  STRAVA_CLIENT_SECRET — from strava.com/settings/api
  STRAVA_REFRESH_TOKEN — obtained after initial OAuth authorization

To get STRAVA_REFRESH_TOKEN:
  1. Go to strava.com/settings/api → create app
  2. Authorize: https://www.strava.com/oauth/authorize?client_id=YOUR_ID&redirect_uri=http://localhost&response_type=code&scope=read,activity:read_all
  3. Exchange the code: POST https://www.strava.com/oauth/token with the code
  4. Use the refresh_token from the response
"""
import os
import httpx
import time
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.types import Tool, TextContent
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route, Mount
import uvicorn

PORT = int(os.environ.get("PORT", 8000))
STRAVA_CLIENT_ID = os.environ.get("STRAVA_CLIENT_ID", "")
STRAVA_CLIENT_SECRET = os.environ.get("STRAVA_CLIENT_SECRET", "")
STRAVA_REFRESH_TOKEN = os.environ.get("STRAVA_REFRESH_TOKEN", "")

server = Server("strava-mcp")
sse_transport = SseServerTransport("/messages/")

STRAVA_BASE = "https://www.strava.com/api/v3"
_token_cache: dict = {}


def _check_config():
    if not all([STRAVA_CLIENT_ID, STRAVA_CLIENT_SECRET, STRAVA_REFRESH_TOKEN]):
        return (
            "⚠️  Strava API not configured. Set these env vars in Render:\n"
            "  STRAVA_CLIENT_ID     — from strava.com/settings/api\n"
            "  STRAVA_CLIENT_SECRET — from strava.com/settings/api\n"
            "  STRAVA_REFRESH_TOKEN — obtained via OAuth (see server docstring for steps)"
        )
    return None


async def _get_access_token() -> str:
    now = time.time()
    if _token_cache.get("expires_at", 0) > now:
        return _token_cache["token"]
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(
            "https://www.strava.com/oauth/token",
            data={
                "client_id": STRAVA_CLIENT_ID,
                "client_secret": STRAVA_CLIENT_SECRET,
                "refresh_token": STRAVA_REFRESH_TOKEN,
                "grant_type": "refresh_token"
            }
        )
    r.raise_for_status()
    d = r.json()
    _token_cache["token"] = d["access_token"]
    _token_cache["expires_at"] = d["expires_at"] - 60
    return _token_cache["token"]


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="get_athlete_profile",
            description="Get the authenticated Strava athlete's profile and stats.",
            inputSchema={"type": "object", "properties": {}, "required": []}
        ),
        Tool(
            name="get_activities",
            description="Get a list of recent activities for the authenticated athlete.",
            inputSchema={
                "type": "object",
                "properties": {
                    "per_page": {"type": "integer", "description": "Activities per page (1-50). Default: 20.", "default": 20},
                    "page": {"type": "integer", "description": "Page number. Default: 1.", "default": 1}
                },
                "required": []
            }
        ),
        Tool(
            name="get_activity_details",
            description="Get detailed information about a specific Strava activity.",
            inputSchema={
                "type": "object",
                "properties": {
                    "activity_id": {"type": "integer", "description": "Strava activity ID (from get_activities)."}
                },
                "required": ["activity_id"]
            }
        ),
        Tool(
            name="get_athlete_stats",
            description="Get overall activity statistics for the athlete (totals for run, ride, swim).",
            inputSchema={"type": "object", "properties": {}, "required": []}
        ),
        Tool(
            name="get_segments",
            description="Get starred segments for the athlete.",
            inputSchema={"type": "object", "properties": {}, "required": []}
        )
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    err = _check_config()
    if err:
        return [TextContent(type="text", text=err)]
    try:
        token = await _get_access_token()
    except Exception as e:
        return [TextContent(type="text", text=f"Auth error: {e}")]

    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=20, headers=headers) as client:
        if name == "get_athlete_profile":
            result = await _get_profile(client)
        elif name == "get_activities":
            result = await _get_activities(client, arguments)
        elif name == "get_activity_details":
            result = await _get_activity(client, arguments)
        elif name == "get_athlete_stats":
            result = await _get_stats(client)
        elif name == "get_segments":
            result = await _get_segments(client)
        else:
            raise ValueError(f"Unknown tool: {name}")
    return [TextContent(type="text", text=result)]


async def _get_profile(client) -> str:
    r = await client.get(f"{STRAVA_BASE}/athlete")
    if r.status_code != 200:
        return f"Error {r.status_code}: {r.text}"
    a = r.json()
    return (
        f"**{a.get('firstname','')} {a.get('lastname','')}** (@{a.get('username','')})\n"
        f"Location: {a.get('city','')}, {a.get('country','')}\n"
        f"Followers: {a.get('follower_count',0)} | Following: {a.get('friend_count',0)}\n"
        f"Member since: {a.get('created_at','')[:10]}\n"
        f"Premium: {'Yes' if a.get('premium') else 'No'}\n"
        f"Weight: {a.get('weight','?')} kg | FTP: {a.get('ftp','?')} W"
    )


async def _get_activities(client, args) -> str:
    r = await client.get(f"{STRAVA_BASE}/athlete/activities", params={
        "per_page": min(int(args.get("per_page", 20)), 50),
        "page": args.get("page", 1)
    })
    if r.status_code != 200:
        return f"Error {r.status_code}: {r.text}"
    activities = r.json()
    if not activities:
        return "No activities found."
    lines = [f"**Recent Activities ({len(activities)}):**\n"]
    for a in activities:
        dist_km = round(a.get("distance", 0) / 1000, 2)
        elapsed = a.get("elapsed_time", 0)
        duration = f"{elapsed//3600}h {(elapsed%3600)//60}m" if elapsed >= 3600 else f"{elapsed//60}m {elapsed%60}s"
        elevation = a.get("total_elevation_gain", 0)
        sport = a.get("sport_type", a.get("type", "?"))
        date = a.get("start_date_local", "")[:10]
        lines.append(
            f"• [{sport}] **{a.get('name','')}** — {date}\n"
            f"  {dist_km} km | {duration} | ↑{elevation:.0f}m | ID: {a.get('id','')}"
        )
    return "\n".join(lines)


async def _get_activity(client, args) -> str:
    aid = args.get("activity_id", "")
    r = await client.get(f"{STRAVA_BASE}/activities/{aid}")
    if r.status_code != 200:
        return f"Error {r.status_code}: {r.text}"
    a = r.json()
    dist_km = round(a.get("distance", 0) / 1000, 2)
    elapsed = a.get("elapsed_time", 0)
    moving = a.get("moving_time", 0)
    duration_e = f"{elapsed//3600}h {(elapsed%3600)//60}m"
    duration_m = f"{moving//3600}h {(moving%3600)//60}m"
    avg_speed_kph = round(a.get("average_speed", 0) * 3.6, 1)
    max_speed_kph = round(a.get("max_speed", 0) * 3.6, 1)
    lines = [
        f"**{a.get('name','')}** ({a.get('sport_type', a.get('type','?'))})",
        f"Date: {a.get('start_date_local','')[:16].replace('T',' ')}",
        f"Distance: {dist_km} km | Elapsed: {duration_e} | Moving: {duration_m}",
        f"Elevation gain: {a.get('total_elevation_gain',0):.0f} m",
        f"Avg speed: {avg_speed_kph} km/h | Max speed: {max_speed_kph} km/h",
        f"Avg HR: {a.get('average_heartrate','N/A')} bpm | Max HR: {a.get('max_heartrate','N/A')} bpm",
        f"Avg power: {a.get('average_watts','N/A')} W | Calories: {a.get('calories','N/A')}",
        f"Kudos: {a.get('kudos_count',0)} | Comments: {a.get('comment_count',0)}",
        f"Description: {a.get('description','') or 'N/A'}",
    ]
    return "\n".join(lines)


async def _get_stats(client) -> str:
    r_athlete = await client.get(f"{STRAVA_BASE}/athlete")
    if r_athlete.status_code != 200:
        return f"Error: {r_athlete.status_code}"
    athlete_id = r_athlete.json().get("id")
    r = await client.get(f"{STRAVA_BASE}/athletes/{athlete_id}/stats")
    if r.status_code != 200:
        return f"Error {r.status_code}: {r.text}"
    s = r.json()

    def fmt_totals(t):
        dist = round(t.get("distance", 0) / 1000, 1)
        time_h = round(t.get("moving_time", 0) / 3600, 1)
        elev = round(t.get("elevation_gain", 0))
        count = t.get("count", 0)
        return f"{count} activities | {dist} km | {time_h} h | ↑{elev} m"

    return (
        f"**Athlete Statistics (All Time):**\n\n"
        f"🚴 **Rides:** {fmt_totals(s.get('all_ride_totals',{}))}\n"
        f"🏃 **Runs:**  {fmt_totals(s.get('all_run_totals',{}))}\n"
        f"🏊 **Swims:** {fmt_totals(s.get('all_swim_totals',{}))}\n\n"
        f"**This Year:**\n"
        f"🚴 Rides: {fmt_totals(s.get('ytd_ride_totals',{}))}\n"
        f"🏃 Runs:  {fmt_totals(s.get('ytd_run_totals',{}))}\n"
        f"🏊 Swims: {fmt_totals(s.get('ytd_swim_totals', {}))}"
    )


async def _get_segments(client) -> str:
    r = await client.get(f"{STRAVA_BASE}/segments/starred", params={"per_page": 20})
    if r.status_code != 200:
        return f"Error {r.status_code}: {r.text}"
    segs = r.json()
    if not segs:
        return "No starred segments found."
    lines = [f"**Starred Segments ({len(segs)}):**\n"]
    for s in segs:
        dist_km = round(s.get("distance", 0) / 1000, 2)
        grade = s.get("average_grade", 0)
        lines.append(f"• **{s.get('name','')}** — {dist_km} km | Grade: {grade:.1f}% | ID: {s.get('id','')}")
    return "\n".join(lines)


async def handle_sse(request: Request):
    async with sse_transport.connect_sse(
        request.scope, request.receive, request._send
    ) as streams:
        await server.run(streams[0], streams[1], server.create_initialization_options())


async def health(request: Request):
    return JSONResponse({"status": "ok", "service": "strava-mcp"})


app = Starlette(
    routes=[
        Route("/health", health),
        Route("/sse", handle_sse),
        Mount("/messages/", app=sse_transport.handle_post_message),
    ]
)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
