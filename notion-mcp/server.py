"""
Notion MCP Server — remote HTTP/SSE endpoint for claude.ai
Uses the official Notion API.

Required env var:
  NOTION_API_KEY — Internal Integration Token from notion.so/my-integrations
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
NOTION_API_KEY = os.environ.get("NOTION_API_KEY", "")

server = Server("notion-mcp")
sse_transport = SseServerTransport("/messages/")

NOTION_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


def _headers():
    return {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json"
    }


def _check_config():
    if not NOTION_API_KEY:
        return (
            "⚠️  Notion API key not configured.\n"
            "1. Go to https://www.notion.so/my-integrations\n"
            "2. Click '+ New integration' → give it a name\n"
            "3. Copy the Internal Integration Token\n"
            "4. Set NOTION_API_KEY in the Render dashboard\n"
            "5. Share your Notion pages/databases with the integration"
        )
    return None


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="search_notion",
            description="Search across all Notion pages and databases accessible to the integration.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query text."},
                    "filter_type": {"type": "string", "description": "Filter by type: 'page' or 'database'. Leave empty for both.", "default": ""}
                },
                "required": ["query"]
            }
        ),
        Tool(
            name="get_page",
            description="Get a Notion page's properties and content blocks.",
            inputSchema={
                "type": "object",
                "properties": {
                    "page_id": {"type": "string", "description": "Notion page ID (32-char hex or UUID format from page URL)."}
                },
                "required": ["page_id"]
            }
        ),
        Tool(
            name="create_page",
            description="Create a new Notion page inside a database or as a child of another page.",
            inputSchema={
                "type": "object",
                "properties": {
                    "parent_id": {"type": "string", "description": "Parent database ID or page ID to create the page in."},
                    "parent_type": {"type": "string", "description": "'database' or 'page'. Default: page.", "default": "page"},
                    "title": {"type": "string", "description": "Page title."},
                    "content": {"type": "string", "description": "Optional page body text (added as paragraph blocks)."},
                    "properties": {"type": "object", "description": "Optional database properties as key-value pairs (for database parents)."}
                },
                "required": ["parent_id", "title"]
            }
        ),
        Tool(
            name="list_databases",
            description="List all Notion databases accessible to the integration.",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
        Tool(
            name="query_database",
            description="Query rows from a Notion database with optional filters.",
            inputSchema={
                "type": "object",
                "properties": {
                    "database_id": {"type": "string", "description": "Notion database ID."},
                    "page_size": {"type": "integer", "description": "Number of rows to return (1-100). Default: 20.", "default": 20}
                },
                "required": ["database_id"]
            }
        )
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    err = _check_config()
    if err:
        return [TextContent(type="text", text=err)]

    async with httpx.AsyncClient(timeout=20, headers=_headers()) as client:
        if name == "search_notion":
            result = await _search(client, arguments)
        elif name == "get_page":
            result = await _get_page(client, arguments)
        elif name == "create_page":
            result = await _create_page(client, arguments)
        elif name == "list_databases":
            result = await _list_databases(client)
        elif name == "query_database":
            result = await _query_database(client, arguments)
        else:
            raise ValueError(f"Unknown tool: {name}")
    return [TextContent(type="text", text=result)]


async def _search(client, args) -> str:
    payload = {"query": args.get("query", "")}
    ft = args.get("filter_type", "")
    if ft in ("page", "database"):
        payload["filter"] = {"value": ft, "property": "object"}
    r = await client.post(f"{NOTION_BASE}/search", json=payload)
    if r.status_code != 200:
        return f"Error {r.status_code}: {r.text}"
    results = r.json().get("results", [])
    if not results:
        return f"No Notion results for '{args.get('query')}'."
    lines = [f"**Notion search: '{args.get('query')}'** ({len(results)} results)\n"]
    for item in results:
        obj_type = item.get("object", "?")
        item_id = item.get("id", "")
        if obj_type == "page":
            props = item.get("properties", {})
            title = ""
            for v in props.values():
                if v.get("type") == "title":
                    title_items = v.get("title", [])
                    title = "".join(t.get("plain_text", "") for t in title_items)
                    break
            if not title:
                title = item.get("url", "Untitled")
            lines.append(f"• [Page] **{title or 'Untitled'}** — ID: `{item_id}`\n  {item.get('url','')}")
        elif obj_type == "database":
            db_title = item.get("title", [])
            title = "".join(t.get("plain_text", "") for t in db_title)
            lines.append(f"• [Database] **{title or 'Untitled'}** — ID: `{item_id}`\n  {item.get('url','')}")
    return "\n".join(lines)


async def _get_page(client, args) -> str:
    pid = args.get("page_id", "").replace("-", "")
    r = await client.get(f"{NOTION_BASE}/pages/{pid}")
    if r.status_code == 404:
        return "Page not found. Make sure the page is shared with your integration."
    if r.status_code != 200:
        return f"Error {r.status_code}: {r.text}"
    page = r.json()
    props = page.get("properties", {})

    # Extract title
    title = "Untitled"
    for v in props.values():
        if v.get("type") == "title":
            title = "".join(t.get("plain_text", "") for t in v.get("title", []))
            break

    lines = [
        f"**{title}**",
        f"ID: {page.get('id','')}",
        f"URL: {page.get('url','')}",
        f"Created: {page.get('created_time','')[:10]}",
        f"Last edited: {page.get('last_edited_time','')[:10]}",
        "\n**Properties:**"
    ]
    for key, val in props.items():
        vtype = val.get("type", "")
        if vtype == "title":
            continue
        try:
            if vtype == "rich_text":
                text = "".join(t.get("plain_text", "") for t in val.get("rich_text", []))
                lines.append(f"  {key}: {text}")
            elif vtype == "select":
                lines.append(f"  {key}: {val.get('select', {}).get('name', 'None') if val.get('select') else 'None'}")
            elif vtype == "multi_select":
                opts = [o.get("name", "") for o in val.get("multi_select", [])]
                lines.append(f"  {key}: {', '.join(opts)}")
            elif vtype in ("number", "checkbox", "date", "url", "email", "phone_number"):
                lines.append(f"  {key}: {val.get(vtype, 'N/A')}")
        except Exception:
            pass

    # Fetch blocks (content)
    r2 = await client.get(f"{NOTION_BASE}/blocks/{pid}/children", params={"page_size": 20})
    if r2.status_code == 200:
        blocks = r2.json().get("results", [])
        if blocks:
            lines.append("\n**Content (first 20 blocks):**")
            for b in blocks:
                btype = b.get("type", "")
                block_data = b.get(btype, {})
                text_items = block_data.get("rich_text", [])
                text = "".join(t.get("plain_text", "") for t in text_items)
                if text:
                    prefix = {"heading_1": "# ", "heading_2": "## ", "heading_3": "### ",
                              "bulleted_list_item": "• ", "numbered_list_item": "1. ",
                              "to_do": f"{'[x]' if block_data.get('checked') else '[ ]'} "}.get(btype, "")
                    lines.append(f"{prefix}{text}")
    return "\n".join(lines)


async def _create_page(client, args) -> str:
    parent_id = args.get("parent_id", "")
    parent_type = args.get("parent_type", "page")
    title = args.get("title", "New Page")
    content = args.get("content", "")

    parent = {"database_id": parent_id} if parent_type == "database" else {"page_id": parent_id}
    properties = {"title": {"title": [{"text": {"content": title}}]}}
    if args.get("properties") and parent_type == "database":
        properties.update(args["properties"])

    children = []
    if content:
        for para in content.split("\n\n"):
            if para.strip():
                children.append({
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {"rich_text": [{"text": {"content": para.strip()[:2000]}}]}
                })

    payload = {"parent": parent, "properties": properties}
    if children:
        payload["children"] = children

    r = await client.post(f"{NOTION_BASE}/pages", json=payload)
    if r.status_code in (200, 201):
        page = r.json()
        return f"✓ Page created: **{title}**\nURL: {page.get('url','')}\nID: {page.get('id','')}"
    return f"Error {r.status_code}: {r.text}"


async def _list_databases(client) -> str:
    r = await client.post(f"{NOTION_BASE}/search", json={"filter": {"value": "database", "property": "object"}})
    if r.status_code != 200:
        return f"Error {r.status_code}: {r.text}"
    dbs = r.json().get("results", [])
    if not dbs:
        return "No databases found. Make sure your integration has been shared with databases."
    lines = [f"**{len(dbs)} Notion Database(s):**\n"]
    for db in dbs:
        title = "".join(t.get("plain_text", "") for t in db.get("title", []))
        lines.append(f"• **{title or 'Untitled'}** — ID: `{db.get('id','')}`\n  {db.get('url','')}")
    return "\n".join(lines)


async def _query_database(client, args) -> str:
    dbid = args.get("database_id", "")
    page_size = min(int(args.get("page_size", 20)), 100)
    r = await client.post(f"{NOTION_BASE}/databases/{dbid}/query", json={"page_size": page_size})
    if r.status_code == 404:
        return "Database not found. Ensure the database is shared with the integration."
    if r.status_code != 200:
        return f"Error {r.status_code}: {r.text}"
    results = r.json().get("results", [])
    if not results:
        return "Database is empty."
    lines = [f"**Database rows ({len(results)}):**\n"]
    for page in results:
        props = page.get("properties", {})
        title = "Untitled"
        for v in props.values():
            if v.get("type") == "title":
                title = "".join(t.get("plain_text", "") for t in v.get("title", []))
                break
        lines.append(f"• **{title}** — ID: `{page.get('id','')}`")
    if r.json().get("has_more"):
        lines.append(f"\n*(More rows available — use pagination)*")
    return "\n".join(lines)


async def handle_sse(request: Request):
    async with sse_transport.connect_sse(
        request.scope, request.receive, request._send
    ) as streams:
        await server.run(streams[0], streams[1], server.create_initialization_options())
    return Response()


async def health(request: Request):
    return JSONResponse({"status": "ok", "service": "notion-mcp"})


app = Starlette(
    routes=[
        Route("/health", health),
        Route("/sse", handle_sse),
        Mount("/messages/", app=sse_transport.handle_post_message),
    ]
)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
