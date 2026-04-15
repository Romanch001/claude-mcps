"""
Wolfram Alpha MCP Server — remote HTTP/SSE endpoint for claude.ai
Endpoint: GET /sse  (paste as URL in claude.ai → Settings → Connectors)
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
WOLFRAM_APP_ID = os.environ.get("WOLFRAM_APP_ID", "")

server = Server("wolfram-alpha-mcp")
sse_transport = SseServerTransport("/messages/")


# ── Tool registry ────────────────────────────────────────────────────────────

@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="query_wolfram_alpha",
            description=(
                "Query Wolfram Alpha for any computational, mathematical, scientific, "
                "unit-conversion, factual, or data question. Examples: "
                "'integrate x^2 from 0 to 3', 'distance from Earth to Mars', "
                "'GDP of India', 'convert 100 USD to INR', 'weather in Mumbai'."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The natural-language or mathematical query to compute."
                    }
                },
                "required": ["query"]
            }
        ),
        Tool(
            name="solve_math",
            description="Solve a mathematical expression or equation step-by-step.",
            inputSchema={
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "Math expression, e.g. 'x^2 + 3x - 4 = 0' or 'derivative of sin(x)*cos(x)'"
                    }
                },
                "required": ["expression"]
            }
        )
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "query_wolfram_alpha":
        result = await _wolfram_query(arguments.get("query", ""))
    elif name == "solve_math":
        result = await _wolfram_query(arguments.get("expression", ""))
    else:
        raise ValueError(f"Unknown tool: {name}")
    return [TextContent(type="text", text=result)]


# ── Core Wolfram Alpha logic ─────────────────────────────────────────────────

async def _wolfram_query(query: str) -> str:
    if not WOLFRAM_APP_ID:
        return (
            "⚠️  WOLFRAM_APP_ID is not configured.\n"
            "Get a free key at: https://developer.wolframalpha.com/access\n"
            "Then set it in Railway → your wolfram-alpha service → Variables."
        )

    async with httpx.AsyncClient(timeout=30) as client:
        # 1. Try the Simple API (short plain-text answer)
        r = await client.get(
            "http://api.wolframalpha.com/v1/result",
            params={"appid": WOLFRAM_APP_ID, "i": query, "units": "metric"}
        )
        if r.status_code == 200:
            return r.text

        # 2. Fall back to the full Query API for richer output
        r2 = await client.get(
            "http://api.wolframalpha.com/v2/query",
            params={
                "appid": WOLFRAM_APP_ID,
                "input": query,
                "output": "json",
                "format": "plaintext"
            }
        )
        if r2.status_code != 200:
            return f"Wolfram Alpha API error: HTTP {r2.status_code}"

        data = r2.json()
        qr = data.get("queryresult", {})
        if not qr.get("success"):
            tips = qr.get("tips", {})
            tip_text = tips.get("text", "") if isinstance(tips, dict) else ""
            return f"Wolfram Alpha could not interpret: '{query}'. {tip_text}"

        parts = []
        for pod in qr.get("pods", []):
            title = pod.get("title", "")
            for sub in pod.get("subpods", []):
                text = sub.get("plaintext", "").strip()
                if text:
                    parts.append(f"**{title}**\n{text}")

        return "\n\n".join(parts) if parts else "No result returned by Wolfram Alpha."


# ── HTTP routes ───────────────────────────────────────────────────────────────

async def handle_sse(request: Request):
    async with sse_transport.connect_sse(
        request.scope, request.receive, request._send
    ) as streams:
        await server.run(streams[0], streams[1], server.create_initialization_options())
    return Response()


async def health(request: Request):
    return JSONResponse({"status": "ok", "service": "wolfram-alpha-mcp"})


app = Starlette(
    routes=[
        Route("/health", health),
        Route("/sse", handle_sse),
        Mount("/messages/", app=sse_transport.handle_post_message),
    ]
)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
