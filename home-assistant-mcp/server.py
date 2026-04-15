"""
Home Assistant MCP Server — remote HTTP/SSE endpoint for claude.ai
Connects to a Home Assistant instance via its REST API.

Required env vars (set in Render dashboard):
  HA_URL   — e.g. https://my-ha.duckdns.org:8123
  HA_TOKEN — Long-lived access token from HA profile
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
HA_URL = os.environ.get("HA_URL", "").rstrip("/")
HA_TOKEN = os.environ.get("HA_TOKEN", "")

server = Server("home-assistant-mcp")
sse_transport = SseServerTransport("/messages/")


def _headers():
    return {"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"}


def _check_config():
    if not HA_URL or not HA_TOKEN:
        return (
            "⚠️  Home Assistant not configured.\n"
            "Set these env vars in the Render dashboard:\n"
            "  HA_URL   = https://your-ha-instance.duckdns.org:8123\n"
            "  HA_TOKEN = <Long-lived access token from HA → Profile → Long-Lived Access Tokens>"
        )
    return None


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="get_ha_states",
            description=(
                "Get all entity states from Home Assistant, optionally filtered by domain "
                "(light, switch, sensor, climate, media_player, etc.)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "domain": {
                        "type": "string",
                        "description": "Filter by domain: 'light', 'switch', 'sensor', 'climate', 'media_player', 'binary_sensor', 'person', 'automation'. Leave empty for all."
                    }
                },
                "required": []
            }
        ),
        Tool(
            name="get_entity_state",
            description="Get the current state and attributes of a specific Home Assistant entity.",
            inputSchema={
                "type": "object",
                "properties": {
                    "entity_id": {
                        "type": "string",
                        "description": "Entity ID, e.g. 'light.living_room', 'sensor.temperature_bedroom', 'switch.kitchen_fan'."
                    }
                },
                "required": ["entity_id"]
            }
        ),
        Tool(
            name="call_service",
            description=(
                "Call a Home Assistant service to control devices. "
                "Examples: turn on/off lights, set thermostat temperature, trigger automations."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "domain": {
                        "type": "string",
                        "description": "Service domain: 'light', 'switch', 'climate', 'media_player', 'automation', 'script'."
                    },
                    "service": {
                        "type": "string",
                        "description": "Service name: 'turn_on', 'turn_off', 'toggle', 'set_temperature', 'media_play_pause'."
                    },
                    "entity_id": {
                        "type": "string",
                        "description": "Target entity ID, e.g. 'light.living_room' or 'switch.fan'."
                    },
                    "extra_data": {
                        "type": "object",
                        "description": "Optional extra service data, e.g. {\"brightness\": 200, \"color_temp\": 4000} for lights."
                    }
                },
                "required": ["domain", "service", "entity_id"]
            }
        ),
        Tool(
            name="get_automations",
            description="List all automations in Home Assistant with their state (enabled/disabled) and last triggered time.",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
        Tool(
            name="get_history",
            description="Get state history for an entity over a time period.",
            inputSchema={
                "type": "object",
                "properties": {
                    "entity_id": {
                        "type": "string",
                        "description": "Entity ID to get history for."
                    },
                    "hours": {
                        "type": "number",
                        "description": "Number of hours of history to retrieve (default: 24).",
                        "default": 24
                    }
                },
                "required": ["entity_id"]
            }
        )
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    err = _check_config()
    if err:
        return [TextContent(type="text", text=err)]

    if name == "get_ha_states":
        result = await _get_states(arguments.get("domain"))
    elif name == "get_entity_state":
        result = await _get_entity(arguments.get("entity_id", ""))
    elif name == "call_service":
        result = await _call_service(
            arguments.get("domain", ""),
            arguments.get("service", ""),
            arguments.get("entity_id", ""),
            arguments.get("extra_data", {})
        )
    elif name == "get_automations":
        result = await _get_automations()
    elif name == "get_history":
        result = await _get_history(arguments.get("entity_id", ""), arguments.get("hours", 24))
    else:
        raise ValueError(f"Unknown tool: {name}")
    return [TextContent(type="text", text=result)]


async def _get_states(domain: str = None) -> str:
    async with httpx.AsyncClient(timeout=15, verify=False) as client:
        r = await client.get(f"{HA_URL}/api/states", headers=_headers())
    if r.status_code != 200:
        return f"Error {r.status_code}: {r.text}"

    states = r.json()
    if domain:
        states = [s for s in states if s["entity_id"].startswith(domain + ".")]

    if not states:
        return f"No entities found{' for domain: ' + domain if domain else ''}."

    lines = [f"Found {len(states)} entities{' in ' + domain if domain else ''}:\n"]
    for s in sorted(states, key=lambda x: x["entity_id"]):
        eid = s["entity_id"]
        state = s["state"]
        attrs = s.get("attributes", {})
        friendly = attrs.get("friendly_name", "")
        name_str = f" ({friendly})" if friendly and friendly != eid else ""
        lines.append(f"• {eid}{name_str}: **{state}**")

    return "\n".join(lines)


async def _get_entity(entity_id: str) -> str:
    async with httpx.AsyncClient(timeout=15, verify=False) as client:
        r = await client.get(f"{HA_URL}/api/states/{entity_id}", headers=_headers())
    if r.status_code == 404:
        return f"Entity '{entity_id}' not found."
    if r.status_code != 200:
        return f"Error {r.status_code}: {r.text}"

    data = r.json()
    lines = [
        f"**{entity_id}**",
        f"State: {data['state']}",
        f"Last changed: {data.get('last_changed', 'unknown')}",
        f"Last updated: {data.get('last_updated', 'unknown')}",
        "\nAttributes:",
    ]
    for k, v in data.get("attributes", {}).items():
        lines.append(f"  {k}: {v}")
    return "\n".join(lines)


async def _call_service(domain: str, service: str, entity_id: str, extra: dict) -> str:
    payload = {"entity_id": entity_id, **(extra or {})}
    async with httpx.AsyncClient(timeout=15, verify=False) as client:
        r = await client.post(
            f"{HA_URL}/api/services/{domain}/{service}",
            headers=_headers(),
            json=payload
        )
    if r.status_code in (200, 201):
        return f"✓ Called {domain}.{service} on {entity_id} successfully."
    return f"Error {r.status_code}: {r.text}"


async def _get_automations() -> str:
    async with httpx.AsyncClient(timeout=15, verify=False) as client:
        r = await client.get(f"{HA_URL}/api/states", headers=_headers())
    if r.status_code != 200:
        return f"Error {r.status_code}: {r.text}"

    autos = [s for s in r.json() if s["entity_id"].startswith("automation.")]
    if not autos:
        return "No automations found."

    lines = [f"**{len(autos)} Automations:**\n"]
    for a in sorted(autos, key=lambda x: x["entity_id"]):
        attrs = a.get("attributes", {})
        state = a["state"]
        last = attrs.get("last_triggered", "never")
        name = attrs.get("friendly_name", a["entity_id"])
        status = "✓" if state == "on" else "✗"
        lines.append(f"{status} **{name}** ({a['entity_id']}) — last triggered: {last}")
    return "\n".join(lines)


async def _get_history(entity_id: str, hours: float = 24) -> str:
    from datetime import datetime, timedelta, timezone
    start = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    async with httpx.AsyncClient(timeout=30, verify=False) as client:
        r = await client.get(
            f"{HA_URL}/api/history/period/{start}",
            headers=_headers(),
            params={"filter_entity_id": entity_id, "minimal_response": "true"}
        )
    if r.status_code != 200:
        return f"Error {r.status_code}: {r.text}"

    history = r.json()
    if not history or not history[0]:
        return f"No history found for '{entity_id}' in the last {hours} hours."

    entries = history[0]
    lines = [f"**History for {entity_id}** (last {hours}h, {len(entries)} points):\n"]
    for entry in entries[-20:]:  # Show last 20 entries
        lines.append(f"  {entry.get('last_changed','?')[:19]}: {entry['state']}")
    if len(entries) > 20:
        lines.append(f"  ... and {len(entries)-20} older entries")
    return "\n".join(lines)


async def handle_sse(request: Request):
    async with sse_transport.connect_sse(
        request.scope, request.receive, request._send
    ) as streams:
        await server.run(streams[0], streams[1], server.create_initialization_options())


async def health(request: Request):
    return JSONResponse({"status": "ok", "service": "home-assistant-mcp"})


app = Starlette(
    routes=[
        Route("/health", health),
        Route("/sse", handle_sse),
        Route("/messages/", sse_transport.handle_post_message),
    ]
)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
