"""
Spotify MCP Server — remote HTTP/SSE endpoint for claude.ai
Uses Spotify Web API with Client Credentials flow (no user login needed).

Required env vars:
  SPOTIFY_CLIENT_ID     — from developer.spotify.com/dashboard
  SPOTIFY_CLIENT_SECRET — from developer.spotify.com/dashboard
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
SPOTIFY_CLIENT_ID = os.environ.get("SPOTIFY_CLIENT_ID", "")
SPOTIFY_CLIENT_SECRET = os.environ.get("SPOTIFY_CLIENT_SECRET", "")

server = Server("spotify-mcp")
sse_transport = SseServerTransport("/messages/")

_token_cache: dict = {}


def _check_config():
    if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
        return (
            "⚠️  Spotify credentials not configured.\n"
            "1. Go to https://developer.spotify.com/dashboard → Create App\n"
            "2. Copy Client ID and Client Secret\n"
            "3. Set SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET in the Render dashboard."
        )
    return None


async def _get_token() -> str:
    import time
    now = time.time()
    if _token_cache.get("expires_at", 0) > now:
        return _token_cache["token"]

    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(
            "https://accounts.spotify.com/api/token",
            data={"grant_type": "client_credentials"},
            auth=(SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET),
        )
    r.raise_for_status()
    data = r.json()
    _token_cache["token"] = data["access_token"]
    _token_cache["expires_at"] = now + data["expires_in"] - 60
    return _token_cache["token"]


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="search_spotify",
            description="Search Spotify for tracks, artists, albums, or playlists.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query, e.g. 'Bohemian Rhapsody', 'The Beatles', 'chill lo-fi beats'."},
                    "type": {"type": "string", "description": "Type to search: 'track', 'artist', 'album', 'playlist'. Default: track.", "default": "track"},
                    "limit": {"type": "integer", "description": "Number of results (1-20). Default: 5.", "default": 5}
                },
                "required": ["query"]
            }
        ),
        Tool(
            name="get_artist",
            description="Get Spotify artist info: bio, genres, popularity, top tracks.",
            inputSchema={
                "type": "object",
                "properties": {
                    "artist_id": {"type": "string", "description": "Spotify artist ID (from search results)."},
                    "country": {"type": "string", "description": "Country code for top tracks, e.g. 'US', 'IN', 'GB'. Default: US.", "default": "US"}
                },
                "required": ["artist_id"]
            }
        ),
        Tool(
            name="get_album",
            description="Get album details and track listing from Spotify.",
            inputSchema={
                "type": "object",
                "properties": {
                    "album_id": {"type": "string", "description": "Spotify album ID."}
                },
                "required": ["album_id"]
            }
        ),
        Tool(
            name="get_recommendations",
            description="Get song recommendations based on seed tracks, artists, or genres.",
            inputSchema={
                "type": "object",
                "properties": {
                    "seed_tracks": {"type": "array", "items": {"type": "string"}, "description": "Up to 2 Spotify track IDs as seeds."},
                    "seed_artists": {"type": "array", "items": {"type": "string"}, "description": "Up to 2 Spotify artist IDs as seeds."},
                    "seed_genres": {"type": "array", "items": {"type": "string"}, "description": "Up to 2 genres: 'pop', 'rock', 'hip-hop', 'jazz', 'classical', 'electronic', 'indie'."},
                    "limit": {"type": "integer", "description": "Number of recommendations (1-20). Default: 10.", "default": 10}
                },
                "required": []
            }
        ),
        Tool(
            name="get_new_releases",
            description="Get new album releases on Spotify.",
            inputSchema={
                "type": "object",
                "properties": {
                    "country": {"type": "string", "description": "Country code, e.g. 'US', 'IN', 'GB'. Default: US.", "default": "US"},
                    "limit": {"type": "integer", "description": "Number of albums (1-20). Default: 10.", "default": 10}
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

    token = await _get_token()
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(timeout=15) as client:
        if name == "search_spotify":
            result = await _search(client, headers, arguments)
        elif name == "get_artist":
            result = await _get_artist(client, headers, arguments)
        elif name == "get_album":
            result = await _get_album(client, headers, arguments)
        elif name == "get_recommendations":
            result = await _get_recommendations(client, headers, arguments)
        elif name == "get_new_releases":
            result = await _get_new_releases(client, headers, arguments)
        else:
            raise ValueError(f"Unknown tool: {name}")

    return [TextContent(type="text", text=result)]


async def _search(client, headers, args) -> str:
    q = args.get("query", "")
    t = args.get("type", "track")
    limit = min(int(args.get("limit", 5)), 20)
    r = await client.get(
        "https://api.spotify.com/v1/search",
        headers=headers,
        params={"q": q, "type": t, "limit": limit}
    )
    if r.status_code != 200:
        return f"Error {r.status_code}: {r.text}"
    data = r.json()

    lines = [f"**Spotify search: '{q}' ({t})**\n"]
    key = t + "s"
    items = data.get(key, {}).get("items", [])
    for item in items:
        if t == "track":
            artists = ", ".join(a["name"] for a in item.get("artists", []))
            album = item.get("album", {}).get("name", "")
            dur_ms = item.get("duration_ms", 0)
            dur = f"{dur_ms//60000}:{(dur_ms%60000)//1000:02d}"
            lines.append(f"• **{item['name']}** — {artists} | {album} | {dur} | ID: `{item['id']}`")
        elif t == "artist":
            genres = ", ".join(item.get("genres", [])[:3]) or "—"
            pop = item.get("popularity", 0)
            lines.append(f"• **{item['name']}** | Genres: {genres} | Popularity: {pop}/100 | ID: `{item['id']}`")
        elif t == "album":
            artists = ", ".join(a["name"] for a in item.get("artists", []))
            year = item.get("release_date", "")[:4]
            tracks = item.get("total_tracks", "?")
            lines.append(f"• **{item['name']}** — {artists} | {year} | {tracks} tracks | ID: `{item['id']}`")
        elif t == "playlist":
            owner = item.get("owner", {}).get("display_name", "?")
            tracks = item.get("tracks", {}).get("total", "?")
            lines.append(f"• **{item['name']}** by {owner} | {tracks} tracks | ID: `{item['id']}`")
    return "\n".join(lines) if items else f"No {t} results for '{q}'."


async def _get_artist(client, headers, args) -> str:
    aid = args.get("artist_id", "")
    country = args.get("country", "US")
    r = await client.get(f"https://api.spotify.com/v1/artists/{aid}", headers=headers)
    r2 = await client.get(f"https://api.spotify.com/v1/artists/{aid}/top-tracks", headers=headers, params={"market": country})
    if r.status_code != 200:
        return f"Error {r.status_code}: {r.text}"
    a = r.json()
    lines = [
        f"**{a['name']}**",
        f"Followers: {a.get('followers',{}).get('total',0):,}",
        f"Popularity: {a.get('popularity',0)}/100",
        f"Genres: {', '.join(a.get('genres',[])[:5]) or 'N/A'}",
        f"Spotify URL: {a.get('external_urls',{}).get('spotify','')}",
        "\n**Top Tracks:**"
    ]
    if r2.status_code == 200:
        for i, t in enumerate(r2.json().get("tracks", [])[:10], 1):
            dur_ms = t.get("duration_ms", 0)
            dur = f"{dur_ms//60000}:{(dur_ms%60000)//1000:02d}"
            lines.append(f"{i}. {t['name']} ({dur}) — ID: `{t['id']}`")
    return "\n".join(lines)


async def _get_album(client, headers, args) -> str:
    alid = args.get("album_id", "")
    r = await client.get(f"https://api.spotify.com/v1/albums/{alid}", headers=headers)
    if r.status_code != 200:
        return f"Error {r.status_code}: {r.text}"
    a = r.json()
    artists = ", ".join(x["name"] for x in a.get("artists", []))
    lines = [
        f"**{a['name']}** — {artists}",
        f"Released: {a.get('release_date','')} | {a.get('total_tracks',0)} tracks | {a.get('album_type','').title()}",
        f"Label: {a.get('label','')}",
        f"Popularity: {a.get('popularity',0)}/100",
        "\n**Tracks:**"
    ]
    for t in a.get("tracks", {}).get("items", []):
        num = t.get("track_number", "?")
        dur_ms = t.get("duration_ms", 0)
        dur = f"{dur_ms//60000}:{(dur_ms%60000)//1000:02d}"
        lines.append(f"  {num}. {t['name']} ({dur}) — ID: `{t['id']}`")
    return "\n".join(lines)


async def _get_recommendations(client, headers, args) -> str:
    params = {"limit": min(int(args.get("limit", 10)), 20)}
    if args.get("seed_tracks"):
        params["seed_tracks"] = ",".join(args["seed_tracks"][:2])
    if args.get("seed_artists"):
        params["seed_artists"] = ",".join(args["seed_artists"][:2])
    if args.get("seed_genres"):
        params["seed_genres"] = ",".join(args["seed_genres"][:2])
    if not any(k in params for k in ("seed_tracks", "seed_artists", "seed_genres")):
        params["seed_genres"] = "pop"
    r = await client.get("https://api.spotify.com/v1/recommendations", headers=headers, params=params)
    if r.status_code != 200:
        return f"Error {r.status_code}: {r.text}"
    lines = ["**Recommended Tracks:**\n"]
    for t in r.json().get("tracks", []):
        artists = ", ".join(a["name"] for a in t.get("artists", []))
        dur_ms = t.get("duration_ms", 0)
        dur = f"{dur_ms//60000}:{(dur_ms%60000)//1000:02d}"
        lines.append(f"• **{t['name']}** — {artists} ({dur}) | ID: `{t['id']}`")
    return "\n".join(lines)


async def _get_new_releases(client, headers, args) -> str:
    r = await client.get(
        "https://api.spotify.com/v1/browse/new-releases",
        headers=headers,
        params={"country": args.get("country", "US"), "limit": min(int(args.get("limit", 10)), 20)}
    )
    if r.status_code != 200:
        return f"Error {r.status_code}: {r.text}"
    lines = ["**New Releases on Spotify:**\n"]
    for a in r.json().get("albums", {}).get("items", []):
        artists = ", ".join(x["name"] for x in a.get("artists", []))
        lines.append(f"• **{a['name']}** — {artists} | {a.get('release_date','')} | ID: `{a['id']}`")
    return "\n".join(lines)


async def handle_sse(request: Request):
    async with sse_transport.connect_sse(
        request.scope, request.receive, request._send
    ) as streams:
        await server.run(streams[0], streams[1], server.create_initialization_options())


async def health(request: Request):
    return JSONResponse({"status": "ok", "service": "spotify-mcp"})


app = Starlette(
    routes=[
        Route("/health", health),
        Route("/sse", handle_sse),
        Route("/messages/", sse_transport.handle_post_message),
    ]
)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
