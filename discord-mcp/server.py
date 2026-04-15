"""
Discord MCP Server — remote HTTP/SSE endpoint for claude.ai
Sends messages via Discord Webhooks. No bot token needed.

Required env var:
  DISCORD_WEBHOOK_URL — from Discord channel → Edit → Integrations → Webhooks
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
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

server = Server("discord-mcp")
sse_transport = SseServerTransport("/messages/")


def _check_config():
    if not DISCORD_WEBHOOK_URL:
        return (
            "⚠️  Discord Webhook not configured.\n"
            "1. Open Discord → channel settings → Integrations → Webhooks → New Webhook\n"
            "2. Copy the Webhook URL\n"
            "3. Set DISCORD_WEBHOOK_URL in the Render dashboard."
        )
    return None


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="send_message",
            description="Send a plain text message to the configured Discord channel via webhook.",
            inputSchema={
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "The message text to send (max 2000 chars)."},
                    "username": {"type": "string", "description": "Override the webhook display name (optional)."},
                    "tts": {"type": "boolean", "description": "Send as text-to-speech message. Default: false.", "default": False}
                },
                "required": ["content"]
            }
        ),
        Tool(
            name="send_embed",
            description="Send a rich embed message to Discord with title, description, color, and fields.",
            inputSchema={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Embed title."},
                    "description": {"type": "string", "description": "Main embed body text (supports Discord markdown)."},
                    "color": {"type": "integer", "description": "Embed sidebar color as decimal integer. e.g. 5763719=green, 15548997=red, 3447003=blue, 16776960=yellow.", "default": 3447003},
                    "fields": {
                        "type": "array",
                        "description": "Optional list of field objects.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "value": {"type": "string"},
                                "inline": {"type": "boolean", "default": False}
                            },
                            "required": ["name", "value"]
                        }
                    },
                    "footer": {"type": "string", "description": "Footer text."},
                    "username": {"type": "string", "description": "Override webhook display name."}
                },
                "required": ["title", "description"]
            }
        ),
        Tool(
            name="send_announcement",
            description="Send a formatted announcement embed to Discord (bold title, highlighted content).",
            inputSchema={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Announcement title."},
                    "message": {"type": "string", "description": "Announcement body."},
                    "mention_everyone": {"type": "boolean", "description": "Add @everyone mention. Default: false.", "default": False},
                    "color": {"type": "string", "description": "Color theme: 'green', 'red', 'blue', 'gold', 'purple'. Default: blue.", "default": "blue"}
                },
                "required": ["title", "message"]
            }
        ),
        Tool(
            name="get_webhook_info",
            description="Get information about the configured Discord webhook (channel name, server).",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        )
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    err = _check_config()
    if err:
        return [TextContent(type="text", text=err)]

    async with httpx.AsyncClient(timeout=15) as client:
        if name == "send_message":
            result = await _send_message(client, arguments)
        elif name == "send_embed":
            result = await _send_embed(client, arguments)
        elif name == "send_announcement":
            result = await _send_announcement(client, arguments)
        elif name == "get_webhook_info":
            result = await _get_info(client)
        else:
            raise ValueError(f"Unknown tool: {name}")
    return [TextContent(type="text", text=result)]


async def _send_message(client, args) -> str:
    payload = {
        "content": args.get("content", "")[:2000],
        "tts": args.get("tts", False)
    }
    if args.get("username"):
        payload["username"] = args["username"]
    r = await client.post(DISCORD_WEBHOOK_URL, json=payload)
    if r.status_code in (200, 204):
        return "✓ Message sent to Discord successfully."
    return f"Error {r.status_code}: {r.text}"


async def _send_embed(client, args) -> str:
    embed = {
        "title": args.get("title", ""),
        "description": args.get("description", ""),
        "color": args.get("color", 3447003),
    }
    if args.get("fields"):
        embed["fields"] = args["fields"]
    if args.get("footer"):
        embed["footer"] = {"text": args["footer"]}
    payload = {"embeds": [embed]}
    if args.get("username"):
        payload["username"] = args["username"]
    r = await client.post(DISCORD_WEBHOOK_URL, json=payload)
    if r.status_code in (200, 204):
        return f"✓ Embed '{args.get('title')}' sent to Discord successfully."
    return f"Error {r.status_code}: {r.text}"


async def _send_announcement(client, args) -> str:
    color_map = {"green": 5763719, "red": 15548997, "blue": 3447003, "gold": 16766720, "purple": 10181046}
    color = color_map.get(args.get("color", "blue"), 3447003)
    content = "@everyone" if args.get("mention_everyone") else None
    embed = {
        "title": f"📢 {args.get('title', '')}",
        "description": args.get("message", ""),
        "color": color,
        "footer": {"text": "Announcement"}
    }
    payload = {"embeds": [embed]}
    if content:
        payload["content"] = content
    r = await client.post(DISCORD_WEBHOOK_URL, json=payload)
    if r.status_code in (200, 204):
        return f"✓ Announcement '{args.get('title')}' sent to Discord."
    return f"Error {r.status_code}: {r.text}"


async def _get_info(client) -> str:
    r = await client.get(DISCORD_WEBHOOK_URL)
    if r.status_code != 200:
        return f"Error {r.status_code}: {r.text}"
    d = r.json()
    return (
        f"**Discord Webhook Info:**\n"
        f"Name: {d.get('name','?')}\n"
        f"Channel ID: {d.get('channel_id','?')}\n"
        f"Guild ID: {d.get('guild_id','?')}\n"
        f"ID: {d.get('id','?')}"
    )


async def handle_sse(request: Request):
    async with sse_transport.connect_sse(
        request.scope, request.receive, request._send
    ) as streams:
        await server.run(streams[0], streams[1], server.create_initialization_options())


async def health(request: Request):
    return JSONResponse({"status": "ok", "service": "discord-mcp"})


app = Starlette(
    routes=[
        Route("/health", health),
        Route("/sse", handle_sse),
        Mount("/messages/", app=sse_transport.handle_post_message),
    ]
)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
