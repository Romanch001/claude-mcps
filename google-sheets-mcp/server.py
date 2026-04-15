"""
Google Sheets MCP Server — remote HTTP/SSE endpoint for claude.ai
Uses Google Sheets API v4 with a Service Account.

Required env var:
  GOOGLE_SERVICE_ACCOUNT_JSON — full JSON content of the service account key file
  (paste the entire JSON as one line or multi-line in Render dashboard)

Setup:
  1. console.cloud.google.com → Enable Google Sheets API
  2. IAM & Admin → Service Accounts → Create → Download JSON key
  3. Share your Google Sheet with the service account email (editor role)
  4. Paste the entire JSON key content as GOOGLE_SERVICE_ACCOUNT_JSON env var
"""
import os
import json
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
GOOGLE_SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")

server = Server("google-sheets-mcp")
sse_transport = SseServerTransport("/messages/")

_token_cache: dict = {}

SHEETS_BASE = "https://sheets.googleapis.com/v4/spreadsheets"
DRIVE_BASE = "https://www.googleapis.com/drive/v3"


def _check_config():
    if not GOOGLE_SERVICE_ACCOUNT_JSON:
        return (
            "⚠️  Google Service Account not configured.\n"
            "1. console.cloud.google.com → Enable Google Sheets API + Google Drive API\n"
            "2. IAM & Admin → Service Accounts → Create → Keys → Add Key → JSON\n"
            "3. Share your spreadsheet with the service account email\n"
            "4. Set GOOGLE_SERVICE_ACCOUNT_JSON env var to the full JSON key content"
        )
    return None


async def _get_access_token() -> str:
    now = time.time()
    if _token_cache.get("expires_at", 0) > now:
        return _token_cache["token"]

    try:
        sa = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
    except json.JSONDecodeError:
        raise ValueError("GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON")

    import base64
    import hashlib
    import hmac

    # Build JWT
    header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256", "typ": "JWT"}).encode()).rstrip(b"=").decode()
    payload_data = {
        "iss": sa["client_email"],
        "scope": "https://www.googleapis.com/auth/spreadsheets https://www.googleapis.com/auth/drive.readonly",
        "aud": "https://oauth2.googleapis.com/token",
        "exp": int(now) + 3600,
        "iat": int(now)
    }
    payload = base64.urlsafe_b64encode(json.dumps(payload_data).encode()).rstrip(b"=").decode()
    signing_input = f"{header}.{payload}".encode()

    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    private_key = serialization.load_pem_private_key(sa["private_key"].encode(), password=None)
    signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    sig_b64 = base64.urlsafe_b64encode(signature).rstrip(b"=").decode()
    jwt_token = f"{header}.{payload}.{sig_b64}"

    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(
            "https://oauth2.googleapis.com/token",
            data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": jwt_token}
        )
    r.raise_for_status()
    token_data = r.json()
    _token_cache["token"] = token_data["access_token"]
    _token_cache["expires_at"] = now + token_data["expires_in"] - 60
    return _token_cache["token"]


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="read_sheet",
            description="Read data from a Google Sheets range.",
            inputSchema={
                "type": "object",
                "properties": {
                    "spreadsheet_id": {"type": "string", "description": "Google Sheets ID from the URL (between /d/ and /edit)."},
                    "range": {"type": "string", "description": "A1 notation range, e.g. 'Sheet1!A1:E10', 'A:Z', 'Sheet2!B2:D20'. Default: Sheet1!A1:Z1000.", "default": "Sheet1!A1:Z1000"}
                },
                "required": ["spreadsheet_id"]
            }
        ),
        Tool(
            name="write_sheet",
            description="Write data to a Google Sheets range (overwrites existing values).",
            inputSchema={
                "type": "object",
                "properties": {
                    "spreadsheet_id": {"type": "string", "description": "Google Sheets ID."},
                    "range": {"type": "string", "description": "Target range in A1 notation, e.g. 'Sheet1!A1'."},
                    "values": {"type": "array", "items": {"type": "array"}, "description": "2D array of values to write, e.g. [[\"Name\",\"Age\"],[\"Alice\",30]]."}
                },
                "required": ["spreadsheet_id", "range", "values"]
            }
        ),
        Tool(
            name="append_rows",
            description="Append rows to the end of data in a Google Sheet.",
            inputSchema={
                "type": "object",
                "properties": {
                    "spreadsheet_id": {"type": "string", "description": "Google Sheets ID."},
                    "sheet_name": {"type": "string", "description": "Sheet/tab name. Default: Sheet1.", "default": "Sheet1"},
                    "values": {"type": "array", "items": {"type": "array"}, "description": "Rows to append, e.g. [[\"Alice\", 30, \"Engineer\"]]."}
                },
                "required": ["spreadsheet_id", "values"]
            }
        ),
        Tool(
            name="list_sheets",
            description="List all sheets/tabs in a Google Spreadsheet.",
            inputSchema={
                "type": "object",
                "properties": {
                    "spreadsheet_id": {"type": "string", "description": "Google Sheets ID."}
                },
                "required": ["spreadsheet_id"]
            }
        ),
        Tool(
            name="clear_range",
            description="Clear values from a range in a Google Sheet.",
            inputSchema={
                "type": "object",
                "properties": {
                    "spreadsheet_id": {"type": "string", "description": "Google Sheets ID."},
                    "range": {"type": "string", "description": "Range to clear, e.g. 'Sheet1!A2:Z100'."}
                },
                "required": ["spreadsheet_id", "range"]
            }
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
        if name == "read_sheet":
            result = await _read_sheet(client, arguments)
        elif name == "write_sheet":
            result = await _write_sheet(client, arguments)
        elif name == "append_rows":
            result = await _append_rows(client, arguments)
        elif name == "list_sheets":
            result = await _list_sheets(client, arguments)
        elif name == "clear_range":
            result = await _clear_range(client, arguments)
        else:
            raise ValueError(f"Unknown tool: {name}")
    return [TextContent(type="text", text=result)]


async def _read_sheet(client, args) -> str:
    sid = args.get("spreadsheet_id", "")
    rng = args.get("range", "Sheet1!A1:Z1000")
    r = await client.get(f"{SHEETS_BASE}/{sid}/values/{rng}")
    if r.status_code != 200:
        return f"Error {r.status_code}: {r.text[:500]}"
    vals = r.json().get("values", [])
    if not vals:
        return "Range is empty."
    lines = [f"**Sheet data ({rng}) — {len(vals)} rows:**\n"]
    for i, row in enumerate(vals[:50]):
        lines.append(f"Row {i+1}: {' | '.join(str(c) for c in row)}")
    if len(vals) > 50:
        lines.append(f"\n... and {len(vals)-50} more rows")
    return "\n".join(lines)


async def _write_sheet(client, args) -> str:
    sid = args.get("spreadsheet_id", "")
    rng = args.get("range", "")
    values = args.get("values", [])
    r = await client.put(
        f"{SHEETS_BASE}/{sid}/values/{rng}",
        params={"valueInputOption": "USER_ENTERED"},
        json={"range": rng, "majorDimension": "ROWS", "values": values}
    )
    if r.status_code == 200:
        updated = r.json().get("updatedCells", 0)
        return f"✓ Written {updated} cells to {rng}."
    return f"Error {r.status_code}: {r.text[:500]}"


async def _append_rows(client, args) -> str:
    sid = args.get("spreadsheet_id", "")
    sheet = args.get("sheet_name", "Sheet1")
    values = args.get("values", [])
    r = await client.post(
        f"{SHEETS_BASE}/{sid}/values/{sheet}!A1:append",
        params={"valueInputOption": "USER_ENTERED", "insertDataOption": "INSERT_ROWS"},
        json={"majorDimension": "ROWS", "values": values}
    )
    if r.status_code == 200:
        updated = r.json().get("updates", {}).get("updatedRows", 0)
        return f"✓ Appended {updated} row(s) to '{sheet}'."
    return f"Error {r.status_code}: {r.text[:500]}"


async def _list_sheets(client, args) -> str:
    sid = args.get("spreadsheet_id", "")
    r = await client.get(f"{SHEETS_BASE}/{sid}", params={"fields": "properties.title,sheets.properties"})
    if r.status_code != 200:
        return f"Error {r.status_code}: {r.text[:500]}"
    d = r.json()
    title = d.get("properties", {}).get("title", "Untitled")
    sheets = d.get("sheets", [])
    lines = [f"**Spreadsheet: {title}**\n{len(sheets)} sheet(s):\n"]
    for s in sheets:
        p = s.get("properties", {})
        lines.append(f"• **{p.get('title','')}** — ID: {p.get('sheetId','')} | {p.get('gridProperties',{}).get('rowCount',0)} rows × {p.get('gridProperties',{}).get('columnCount',0)} cols")
    return "\n".join(lines)


async def _clear_range(client, args) -> str:
    sid = args.get("spreadsheet_id", "")
    rng = args.get("range", "")
    r = await client.post(f"{SHEETS_BASE}/{sid}/values/{rng}:clear")
    if r.status_code == 200:
        return f"✓ Cleared range {rng}."
    return f"Error {r.status_code}: {r.text[:500]}"


async def handle_sse(request: Request):
    async with sse_transport.connect_sse(
        request.scope, request.receive, request._send
    ) as streams:
        await server.run(streams[0], streams[1], server.create_initialization_options())


async def health(request: Request):
    return JSONResponse({"status": "ok", "service": "google-sheets-mcp"})


app = Starlette(
    routes=[
        Route("/health", health),
        Route("/sse", handle_sse),
        Mount("/messages/", app=sse_transport.handle_post_message),
    ]
)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
