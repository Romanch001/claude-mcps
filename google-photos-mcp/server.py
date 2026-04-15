"""
Google Photos MCP Server — remote HTTP/SSE endpoint for claude.ai
Uses Google Photos Library API with a user OAuth access token.

Required env var:
  GOOGLE_PHOTOS_ACCESS_TOKEN — OAuth 2.0 access token with photoslibrary.readonly scope

To get a token:
  1. console.cloud.google.com → Enable Photos Library API
  2. OAuth 2.0 credentials → OAuth Playground (oauth2.googleapis.com/tokeninfo)
  3. Or use: https://developers.google.com/oauthplayground/
     Select scope: https://www.googleapis.com/auth/photoslibrary.readonly
  4. Set GOOGLE_PHOTOS_ACCESS_TOKEN in Render dashboard
  Note: Tokens expire after 1 hour — use a refresh token approach for production.
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
GOOGLE_PHOTOS_ACCESS_TOKEN = os.environ.get("GOOGLE_PHOTOS_ACCESS_TOKEN", "")

server = Server("google-photos-mcp")
sse_transport = SseServerTransport("/messages/")

PHOTOS_BASE = "https://photoslibrary.googleapis.com/v1"


def _check_config():
    if not GOOGLE_PHOTOS_ACCESS_TOKEN:
        return (
            "⚠️  Google Photos Access Token not configured.\n"
            "Setup steps:\n"
            "1. Go to https://console.cloud.google.com/ → Enable Photos Library API\n"
            "2. Create OAuth 2.0 credentials (Desktop app)\n"
            "3. Use OAuth Playground: https://developers.google.com/oauthplayground/\n"
            "   - Add scope: https://www.googleapis.com/auth/photoslibrary.readonly\n"
            "   - Exchange auth code for tokens\n"
            "4. Set GOOGLE_PHOTOS_ACCESS_TOKEN in Render dashboard\n"
            "⚠️  Note: Access tokens expire after 1 hour."
        )
    return None


def _headers():
    return {"Authorization": f"Bearer {GOOGLE_PHOTOS_ACCESS_TOKEN}"}


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="list_albums",
            description="List all albums in the user's Google Photos library.",
            inputSchema={
                "type": "object",
                "properties": {
                    "page_size": {"type": "integer", "description": "Number of albums to return (1-50). Default: 20.", "default": 20}
                },
                "required": []
            }
        ),
        Tool(
            name="get_album_photos",
            description="Get photos from a specific Google Photos album.",
            inputSchema={
                "type": "object",
                "properties": {
                    "album_id": {"type": "string", "description": "Album ID from list_albums."},
                    "page_size": {"type": "integer", "description": "Number of photos to return (1-100). Default: 25.", "default": 25}
                },
                "required": ["album_id"]
            }
        ),
        Tool(
            name="search_photos",
            description="Search photos by date range, content categories, or text filters.",
            inputSchema={
                "type": "object",
                "properties": {
                    "content_categories": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Filter by content: 'ANIMALS', 'ARTS', 'BIRTHDAYS', 'CITYSCAPES', 'CRAFTS', 'DOCUMENTS', 'FASHION', 'FLOWERS', 'FOOD', 'GARDENS', 'HOLIDAYS', 'HOUSES', 'LANDMARKS', 'LANDSCAPES', 'NIGHT', 'PEOPLE', 'PETS', 'PERFORMANCES', 'RECEIPTS', 'SCREENSHOTS', 'SELFIES', 'SPORT', 'TRAVEL', 'WEDDINGS', 'WHITEBOARDS'."
                    },
                    "date_from": {"type": "string", "description": "Start date YYYY-MM-DD (optional)."},
                    "date_to": {"type": "string", "description": "End date YYYY-MM-DD (optional)."},
                    "page_size": {"type": "integer", "description": "Number of results (1-100). Default: 25.", "default": 25}
                },
                "required": []
            }
        ),
        Tool(
            name="get_recent_photos",
            description="Get the most recently uploaded photos from Google Photos.",
            inputSchema={
                "type": "object",
                "properties": {
                    "page_size": {"type": "integer", "description": "Number of photos to return (1-100). Default: 20.", "default": 20}
                },
                "required": []
            }
        )
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    err = _check_config()
    if err:
        return [TextContent(type="text", text=err)]

    async with httpx.AsyncClient(timeout=20, headers=_headers()) as client:
        if name == "list_albums":
            result = await _list_albums(client, arguments)
        elif name == "get_album_photos":
            result = await _get_album_photos(client, arguments)
        elif name == "search_photos":
            result = await _search_photos(client, arguments)
        elif name == "get_recent_photos":
            result = await _get_recent(client, arguments)
        else:
            raise ValueError(f"Unknown tool: {name}")
    return [TextContent(type="text", text=result)]


async def _list_albums(client, args) -> str:
    r = await client.get(f"{PHOTOS_BASE}/albums", params={"pageSize": min(int(args.get("page_size", 20)), 50)})
    if r.status_code == 401:
        return "❌ Access token expired or invalid. Please refresh GOOGLE_PHOTOS_ACCESS_TOKEN."
    if r.status_code != 200:
        return f"Error {r.status_code}: {r.text[:500]}"
    albums = r.json().get("albums", [])
    if not albums:
        return "No albums found in Google Photos."
    lines = [f"**Google Photos Albums ({len(albums)}):**\n"]
    for a in albums:
        count = a.get("mediaItemsCount", "?")
        lines.append(f"• **{a.get('title', 'Untitled')}** — {count} items | ID: `{a.get('id','')}`")
    return "\n".join(lines)


async def _get_album_photos(client, args) -> str:
    album_id = args.get("album_id", "")
    page_size = min(int(args.get("page_size", 25)), 100)
    r = await client.post(
        f"{PHOTOS_BASE}/mediaItems:search",
        json={"albumId": album_id, "pageSize": page_size}
    )
    if r.status_code == 401:
        return "❌ Access token expired. Please refresh GOOGLE_PHOTOS_ACCESS_TOKEN."
    if r.status_code != 200:
        return f"Error {r.status_code}: {r.text[:500]}"
    items = r.json().get("mediaItems", [])
    if not items:
        return "No photos in this album."
    lines = [f"**Album Photos ({len(items)}):**\n"]
    for item in items:
        meta = item.get("mediaMetadata", {})
        created = meta.get("creationTime", "")[:10]
        w, h = meta.get("width", "?"), meta.get("height", "?")
        filename = item.get("filename", "unknown")
        lines.append(f"• **{filename}** ({w}×{h}) — {created}\n  URL: {item.get('productUrl','')}")
    return "\n".join(lines)


async def _search_photos(client, args) -> str:
    filters = {}
    if args.get("content_categories"):
        filters["contentFilter"] = {"includedContentCategories": args["content_categories"]}
    if args.get("date_from") or args.get("date_to"):
        date_filter = {}
        if args.get("date_from"):
            y, m, d = args["date_from"].split("-")
            date_filter["ranges"] = [{"startDate": {"year": int(y), "month": int(m), "day": int(d)}}]
            if args.get("date_to"):
                y2, m2, d2 = args["date_to"].split("-")
                date_filter["ranges"][0]["endDate"] = {"year": int(y2), "month": int(m2), "day": int(d2)}
        filters["dateFilter"] = date_filter

    payload = {"pageSize": min(int(args.get("page_size", 25)), 100)}
    if filters:
        payload["filters"] = filters

    r = await client.post(f"{PHOTOS_BASE}/mediaItems:search", json=payload)
    if r.status_code == 401:
        return "❌ Access token expired. Please refresh GOOGLE_PHOTOS_ACCESS_TOKEN."
    if r.status_code != 200:
        return f"Error {r.status_code}: {r.text[:500]}"
    items = r.json().get("mediaItems", [])
    if not items:
        return "No photos matched the search criteria."
    lines = [f"**Photo Search Results ({len(items)}):**\n"]
    for item in items:
        meta = item.get("mediaMetadata", {})
        created = meta.get("creationTime", "")[:10]
        filename = item.get("filename", "unknown")
        desc = item.get("description", "")
        lines.append(f"• **{filename}** — {created}{' — ' + desc if desc else ''}\n  {item.get('productUrl','')}")
    return "\n".join(lines)


async def _get_recent(client, args) -> str:
    r = await client.post(
        f"{PHOTOS_BASE}/mediaItems:search",
        json={"pageSize": min(int(args.get("page_size", 20)), 100)}
    )
    if r.status_code == 401:
        return "❌ Access token expired. Please refresh GOOGLE_PHOTOS_ACCESS_TOKEN."
    if r.status_code != 200:
        return f"Error {r.status_code}: {r.text[:500]}"
    items = r.json().get("mediaItems", [])
    if not items:
        return "No photos found."
    lines = [f"**Recent Photos ({len(items)}):**\n"]
    for item in items:
        meta = item.get("mediaMetadata", {})
        created = meta.get("creationTime", "")[:19].replace("T", " ")
        filename = item.get("filename", "unknown")
        w, h = meta.get("width","?"), meta.get("height","?")
        lines.append(f"• **{filename}** ({w}×{h}) — {created}")
    return "\n".join(lines)


async def handle_sse(request: Request):
    async with sse_transport.connect_sse(
        request.scope, request.receive, request._send
    ) as streams:
        await server.run(streams[0], streams[1], server.create_initialization_options())


async def health(request: Request):
    return JSONResponse({"status": "ok", "service": "google-photos-mcp"})


app = Starlette(
    routes=[
        Route("/health", health),
        Route("/sse", handle_sse),
        Route("/messages/", sse_transport.handle_post_message),
    ]
)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
