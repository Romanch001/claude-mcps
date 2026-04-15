"""
Wikipedia MCP Server — remote HTTP/SSE endpoint for claude.ai
Uses the free Wikipedia MediaWiki REST API. No API key required.
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
server = Server("wikipedia-mcp")
sse_transport = SseServerTransport("/messages/")

WIKI_REST = "https://en.wikipedia.org/api/rest_v1"
WIKI_API  = "https://en.wikipedia.org/w/api.php"
HEADERS = {"User-Agent": "claude-mcp-wikipedia/1.0"}


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="search_wikipedia",
            description="Search Wikipedia for articles matching a query.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query, e.g. 'quantum entanglement', 'French Revolution', 'Python programming language'."},
                    "limit": {"type": "integer", "description": "Number of results (1-10). Default: 5.", "default": 5},
                    "language": {"type": "string", "description": "Wikipedia language code: 'en', 'hi', 'fr', 'de', 'es', 'ja'. Default: en.", "default": "en"}
                },
                "required": ["query"]
            }
        ),
        Tool(
            name="get_article_summary",
            description="Get a concise summary (intro paragraph) of a Wikipedia article.",
            inputSchema={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Exact Wikipedia article title, e.g. 'Albert Einstein', 'Python (programming language)'."},
                    "language": {"type": "string", "description": "Wikipedia language code. Default: en.", "default": "en"}
                },
                "required": ["title"]
            }
        ),
        Tool(
            name="get_article_sections",
            description="Get the full section outline and content of a Wikipedia article.",
            inputSchema={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Wikipedia article title."},
                    "section": {"type": "string", "description": "Specific section name to retrieve (optional). Leave empty for table of contents."},
                    "language": {"type": "string", "description": "Wikipedia language code. Default: en.", "default": "en"}
                },
                "required": ["title"]
            }
        ),
        Tool(
            name="get_related_articles",
            description="Get articles related to or linked from a Wikipedia page.",
            inputSchema={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Wikipedia article title."},
                    "limit": {"type": "integer", "description": "Number of related articles (1-20). Default: 10.", "default": 10}
                },
                "required": ["title"]
            }
        ),
        Tool(
            name="get_on_this_day",
            description="Get Wikipedia 'On This Day' events for a given date.",
            inputSchema={
                "type": "object",
                "properties": {
                    "month": {"type": "integer", "description": "Month (1-12)."},
                    "day": {"type": "integer", "description": "Day (1-31)."}
                },
                "required": ["month", "day"]
            }
        )
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    lang = arguments.get("language", "en")
    async with httpx.AsyncClient(timeout=20, headers=HEADERS) as client:
        if name == "search_wikipedia":
            result = await _search(client, arguments, lang)
        elif name == "get_article_summary":
            result = await _summary(client, arguments, lang)
        elif name == "get_article_sections":
            result = await _sections(client, arguments, lang)
        elif name == "get_related_articles":
            result = await _related(client, arguments)
        elif name == "get_on_this_day":
            result = await _on_this_day(client, arguments)
        else:
            raise ValueError(f"Unknown tool: {name}")
    return [TextContent(type="text", text=result)]


async def _search(client, args, lang) -> str:
    q = args.get("query", "")
    limit = min(int(args.get("limit", 5)), 10)
    r = await client.get(
        f"https://{lang}.wikipedia.org/w/api.php",
        params={"action": "query", "list": "search", "srsearch": q, "srlimit": limit, "format": "json"}
    )
    if r.status_code != 200:
        return f"Search error: HTTP {r.status_code}"
    results = r.json().get("query", {}).get("search", [])
    if not results:
        return f"No Wikipedia articles found for '{q}'."
    lines = [f"**Wikipedia search: '{q}'**\n"]
    for item in results:
        title = item["title"]
        snippet = item.get("snippet", "").replace('<span class="searchmatch">', "**").replace("</span>", "**")
        # Strip remaining HTML tags simply
        import re
        snippet = re.sub(r"<[^>]+>", "", snippet)
        lines.append(f"• **{title}**\n  {snippet}\n  https://{lang}.wikipedia.org/wiki/{title.replace(' ', '_')}")
    return "\n".join(lines)


async def _summary(client, args, lang) -> str:
    title = args.get("title", "").replace(" ", "_")
    r = await client.get(f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{title}")
    if r.status_code == 404:
        return f"Article '{args.get('title')}' not found. Try a slightly different title or use search_wikipedia first."
    if r.status_code != 200:
        return f"Error {r.status_code}: {r.text}"
    d = r.json()
    lines = [
        f"**{d.get('title', '')}**",
        f"*{d.get('description', '')}*" if d.get("description") else "",
        "",
        d.get("extract", "No summary available."),
        "",
        f"Full article: {d.get('content_urls', {}).get('desktop', {}).get('page', '')}"
    ]
    return "\n".join(l for l in lines)


async def _sections(client, args, lang) -> str:
    title = args.get("title", "").replace(" ", "_")
    section = args.get("section", "")
    r = await client.get(
        f"https://{lang}.wikipedia.org/w/api.php",
        params={
            "action": "parse", "page": title,
            "prop": "sections|wikitext", "format": "json",
            "redirects": True
        }
    )
    if r.status_code != 200:
        return f"Error {r.status_code}"
    data = r.json()
    if "error" in data:
        return f"Article not found: {data['error'].get('info', '')}"

    sections = data.get("parse", {}).get("sections", [])
    if not sections:
        return f"No sections found for '{args.get('title')}'."

    if not section:
        lines = [f"**{args.get('title')} — Table of Contents:**\n"]
        for s in sections:
            indent = "  " * (int(s.get("toclevel", 1)) - 1)
            lines.append(f"{indent}{s.get('number','')}. {s.get('line','')}")
        return "\n".join(lines)

    # Find the requested section
    sec_idx = None
    for s in sections:
        if section.lower() in s.get("line", "").lower():
            sec_idx = s.get("index")
            break
    if sec_idx is None:
        return f"Section '{section}' not found. Available sections:\n" + "\n".join(s["line"] for s in sections)

    r2 = await client.get(
        f"https://{lang}.wikipedia.org/w/api.php",
        params={"action": "parse", "page": title, "prop": "wikitext", "section": sec_idx, "format": "json"}
    )
    text = r2.json().get("parse", {}).get("wikitext", {}).get("*", "")
    import re
    text = re.sub(r"\[\[([^|\]]+\|)?([^\]]+)\]\]", r"\2", text)
    text = re.sub(r"\{\{[^}]+\}\}", "", text)
    text = re.sub(r"<[^>]+>", "", text)
    return f"**{args.get('title')} — {section}:**\n\n{text[:3000]}"


async def _related(client, args) -> str:
    title = args.get("title", "")
    limit = min(int(args.get("limit", 10)), 20)
    r = await client.get(
        WIKI_API,
        headers=HEADERS,
        params={
            "action": "query", "titles": title,
            "prop": "links", "pllimit": limit,
            "plnamespace": 0, "format": "json"
        }
    )
    if r.status_code != 200:
        return f"Error {r.status_code}"
    pages = r.json().get("query", {}).get("pages", {})
    links = []
    for page in pages.values():
        links = [l["title"] for l in page.get("links", [])]
    if not links:
        return f"No related articles found for '{title}'."
    lines = [f"**Articles linked from '{title}':**\n"]
    for link in links[:limit]:
        lines.append(f"• {link} — https://en.wikipedia.org/wiki/{link.replace(' ', '_')}")
    return "\n".join(lines)


async def _on_this_day(client, args) -> str:
    month = args.get("month", 1)
    day = args.get("day", 1)
    r = await client.get(f"{WIKI_REST}/feed/onthisday/events/{month}/{day}")
    if r.status_code != 200:
        return f"Error {r.status_code}"
    events = r.json().get("events", [])[:10]
    if not events:
        return f"No events found for {month}/{day}."
    lines = [f"**On This Day — {month}/{day}:**\n"]
    for e in events:
        year = e.get("year", "?")
        text = e.get("text", "")
        lines.append(f"• **{year}**: {text}")
    return "\n".join(lines)


async def handle_sse(request: Request):
    async with sse_transport.connect_sse(
        request.scope, request.receive, request._send
    ) as streams:
        await server.run(streams[0], streams[1], server.create_initialization_options())


async def health(request: Request):
    return JSONResponse({"status": "ok", "service": "wikipedia-mcp"})


app = Starlette(
    routes=[
        Route("/health", health),
        Route("/sse", handle_sse),
        Mount("/messages/", app=sse_transport.handle_post_message),
    ]
)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
