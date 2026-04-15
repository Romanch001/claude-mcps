"""
Reddit MCP Server — remote HTTP/SSE endpoint for claude.ai
Uses Reddit's public JSON API — no API key required for reading public data.
"""
import os
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
server = Server("reddit-mcp")
sse_transport = SseServerTransport("/messages/")

HEADERS = {"User-Agent": "claude-mcp-reddit/1.0 (by /u/claude_mcp_bot)"}


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="get_subreddit_posts",
            description="Get top/hot/new posts from any subreddit.",
            inputSchema={
                "type": "object",
                "properties": {
                    "subreddit": {"type": "string", "description": "Subreddit name without r/, e.g. 'python', 'worldnews', 'AskReddit'."},
                    "sort": {"type": "string", "description": "Sort order: 'hot', 'new', 'top', 'rising'. Default: hot.", "default": "hot"},
                    "limit": {"type": "integer", "description": "Number of posts (1-25). Default: 10.", "default": 10},
                    "time": {"type": "string", "description": "For 'top' sort: 'hour', 'day', 'week', 'month', 'year', 'all'. Default: day.", "default": "day"}
                },
                "required": ["subreddit"]
            }
        ),
        Tool(
            name="search_reddit",
            description="Search Reddit posts across all subreddits or within a specific subreddit.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query."},
                    "subreddit": {"type": "string", "description": "Limit search to this subreddit (optional). Leave empty to search all of Reddit."},
                    "sort": {"type": "string", "description": "Sort: 'relevance', 'hot', 'new', 'top'. Default: relevance.", "default": "relevance"},
                    "limit": {"type": "integer", "description": "Number of results (1-25). Default: 10.", "default": 10}
                },
                "required": ["query"]
            }
        ),
        Tool(
            name="get_post_comments",
            description="Get top comments from a Reddit post.",
            inputSchema={
                "type": "object",
                "properties": {
                    "post_url": {"type": "string", "description": "Full Reddit post URL, e.g. 'https://www.reddit.com/r/Python/comments/abc123/title/'"},
                    "limit": {"type": "integer", "description": "Number of top comments to fetch (1-20). Default: 10.", "default": 10}
                },
                "required": ["post_url"]
            }
        ),
        Tool(
            name="get_subreddit_info",
            description="Get information about a subreddit: description, subscriber count, rules.",
            inputSchema={
                "type": "object",
                "properties": {
                    "subreddit": {"type": "string", "description": "Subreddit name without r/."}
                },
                "required": ["subreddit"]
            }
        ),
        Tool(
            name="get_user_profile",
            description="Get a Reddit user's public profile: karma, post history summary.",
            inputSchema={
                "type": "object",
                "properties": {
                    "username": {"type": "string", "description": "Reddit username without u/."}
                },
                "required": ["username"]
            }
        )
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    async with httpx.AsyncClient(timeout=20, headers=HEADERS, follow_redirects=True) as client:
        if name == "get_subreddit_posts":
            result = await _get_posts(client, arguments)
        elif name == "search_reddit":
            result = await _search(client, arguments)
        elif name == "get_post_comments":
            result = await _get_comments(client, arguments)
        elif name == "get_subreddit_info":
            result = await _sub_info(client, arguments)
        elif name == "get_user_profile":
            result = await _user_profile(client, arguments)
        else:
            raise ValueError(f"Unknown tool: {name}")
    return [TextContent(type="text", text=result)]


async def _get_posts(client, args) -> str:
    sub = args.get("subreddit", "").strip().lstrip("r/")
    sort = args.get("sort", "hot")
    limit = min(int(args.get("limit", 10)), 25)
    time = args.get("time", "day")
    params = {"limit": limit}
    if sort == "top":
        params["t"] = time
    r = await client.get(f"https://www.reddit.com/r/{sub}/{sort}.json", params=params)
    if r.status_code != 200:
        return f"Error fetching r/{sub}: HTTP {r.status_code}. The subreddit may be private or non-existent."
    posts = r.json().get("data", {}).get("children", [])
    if not posts:
        return f"No posts found in r/{sub}."
    lines = [f"**r/{sub} — {sort.upper()} posts:**\n"]
    for p in posts:
        d = p["data"]
        score = d.get("score", 0)
        comments = d.get("num_comments", 0)
        flair = f" [{d['link_flair_text']}]" if d.get("link_flair_text") else ""
        nsfw = " [NSFW]" if d.get("over_18") else ""
        lines.append(
            f"• **{d['title']}**{flair}{nsfw}\n"
            f"  ↑{score:,} | 💬{comments:,} | u/{d.get('author','?')} | "
            f"https://reddit.com{d.get('permalink','')}"
        )
    return "\n".join(lines)


async def _search(client, args) -> str:
    query = args.get("query", "")
    sub = args.get("subreddit", "").strip().lstrip("r/")
    sort = args.get("sort", "relevance")
    limit = min(int(args.get("limit", 10)), 25)
    if sub:
        url = f"https://www.reddit.com/r/{sub}/search.json"
        params = {"q": query, "sort": sort, "limit": limit, "restrict_sr": "on"}
    else:
        url = "https://www.reddit.com/search.json"
        params = {"q": query, "sort": sort, "limit": limit}
    r = await client.get(url, params=params)
    if r.status_code != 200:
        return f"Search error: HTTP {r.status_code}"
    posts = r.json().get("data", {}).get("children", [])
    if not posts:
        return f"No results for '{query}'."
    lines = [f"**Reddit search: '{query}'**{' in r/' + sub if sub else ''}\n"]
    for p in posts:
        d = p["data"]
        lines.append(
            f"• **{d['title']}** (r/{d.get('subreddit','?')})\n"
            f"  ↑{d.get('score',0):,} | u/{d.get('author','?')} | "
            f"https://reddit.com{d.get('permalink','')}"
        )
    return "\n".join(lines)


async def _get_comments(client, args) -> str:
    url = args.get("post_url", "").rstrip("/") + ".json"
    limit = min(int(args.get("limit", 10)), 20)
    r = await client.get(url, params={"limit": limit, "depth": 1, "sort": "top"})
    if r.status_code != 200:
        return f"Error fetching post: HTTP {r.status_code}"
    try:
        data = r.json()
        post = data[0]["data"]["children"][0]["data"]
        comments_data = data[1]["data"]["children"]
    except (KeyError, IndexError):
        return "Could not parse Reddit response."

    lines = [
        f"**{post.get('title','?')}**",
        f"↑{post.get('score',0):,} | r/{post.get('subreddit','?')} | u/{post.get('author','?')}",
        f"\n{post.get('selftext','')[:500]}" if post.get("selftext") else "",
        "\n**Top Comments:**\n"
    ]
    for c in comments_data[:limit]:
        if c.get("kind") != "t1":
            continue
        cd = c["data"]
        body = cd.get("body", "").strip()[:300]
        if body:
            lines.append(f"• u/{cd.get('author','?')} (↑{cd.get('score',0):,}):\n  {body}\n")
    return "\n".join(lines)


async def _sub_info(client, args) -> str:
    sub = args.get("subreddit", "").strip().lstrip("r/")
    r = await client.get(f"https://www.reddit.com/r/{sub}/about.json")
    if r.status_code != 200:
        return f"Error: HTTP {r.status_code}. Subreddit may be private or non-existent."
    d = r.json().get("data", {})
    lines = [
        f"**r/{sub}**",
        f"Title: {d.get('title','')}",
        f"Subscribers: {d.get('subscribers',0):,}",
        f"Active users: {d.get('active_user_count',0):,}",
        f"Created: {__import__('datetime').datetime.fromtimestamp(d.get('created_utc',0)).strftime('%Y-%m-%d')}",
        f"Type: {d.get('subreddit_type','').title()}",
        f"NSFW: {'Yes' if d.get('over18') else 'No'}",
        f"\n**Description:**\n{d.get('public_description','N/A')[:500]}"
    ]
    return "\n".join(l for l in lines if l)


async def _user_profile(client, args) -> str:
    user = args.get("username", "").strip().lstrip("u/")
    r = await client.get(f"https://www.reddit.com/user/{user}/about.json")
    if r.status_code != 200:
        return f"Error: HTTP {r.status_code}"
    d = r.json().get("data", {})
    created = __import__("datetime").datetime.fromtimestamp(d.get("created_utc", 0)).strftime("%Y-%m-%d")
    lines = [
        f"**u/{user}**",
        f"Link karma: {d.get('link_karma',0):,}",
        f"Comment karma: {d.get('comment_karma',0):,}",
        f"Total karma: {d.get('total_karma', d.get('link_karma',0)+d.get('comment_karma',0)):,}",
        f"Account created: {created}",
        f"Verified: {'Yes' if d.get('verified') else 'No'}",
        f"Premium: {'Yes' if d.get('is_gold') else 'No'}",
    ]
    return "\n".join(lines)


async def handle_sse(request: Request):
    async with sse_transport.connect_sse(
        request.scope, request.receive, request._send
    ) as streams:
        await server.run(streams[0], streams[1], server.create_initialization_options())
    return Response()


async def health(request: Request):
    return JSONResponse({"status": "ok", "service": "reddit-mcp"})


app = Starlette(
    routes=[
        Route("/health", health),
        Route("/sse", handle_sse),
        Mount("/messages/", app=sse_transport.handle_post_message),
    ]
)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
