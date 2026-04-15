"""
Google Translate MCP Server — remote HTTP/SSE endpoint for claude.ai
Uses Google Cloud Translation API v2 (Basic).

Required env var:
  GOOGLE_TRANSLATE_API_KEY — from console.cloud.google.com
  Enable: Cloud Translation API (free tier: 500,000 chars/month)
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
GOOGLE_TRANSLATE_API_KEY = os.environ.get("GOOGLE_TRANSLATE_API_KEY", "")

server = Server("google-translate-mcp")
sse_transport = SseServerTransport("/messages/")

TRANSLATE_BASE = "https://translation.googleapis.com/language/translate/v2"


def _check_config():
    if not GOOGLE_TRANSLATE_API_KEY:
        return (
            "⚠️  Google Translate API key not configured.\n"
            "1. Go to https://console.cloud.google.com/\n"
            "2. Enable 'Cloud Translation API'\n"
            "3. Credentials → Create API Key\n"
            "4. Set GOOGLE_TRANSLATE_API_KEY in the Render dashboard.\n"
            "Free tier: 500,000 characters/month at no charge."
        )
    return None


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="translate_text",
            description="Translate text to a target language using Google Translate.",
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text to translate (can be multiple paragraphs)."},
                    "target_language": {"type": "string", "description": "Target language code: 'en', 'hi', 'fr', 'de', 'es', 'ja', 'zh', 'ar', 'pt', 'ru', 'ko', 'it', 'mr', 'ta', 'te', 'gu', 'bn', 'ur'."},
                    "source_language": {"type": "string", "description": "Source language code (optional — auto-detected if omitted)."}
                },
                "required": ["text", "target_language"]
            }
        ),
        Tool(
            name="detect_language",
            description="Detect the language of a given text.",
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text to detect language for."}
                },
                "required": ["text"]
            }
        ),
        Tool(
            name="translate_batch",
            description="Translate multiple texts at once to the same target language.",
            inputSchema={
                "type": "object",
                "properties": {
                    "texts": {"type": "array", "items": {"type": "string"}, "description": "List of strings to translate."},
                    "target_language": {"type": "string", "description": "Target language code."},
                    "source_language": {"type": "string", "description": "Source language code (optional)."}
                },
                "required": ["texts", "target_language"]
            }
        ),
        Tool(
            name="list_languages",
            description="List all languages supported by Google Translate with their names.",
            inputSchema={
                "type": "object",
                "properties": {
                    "display_language": {"type": "string", "description": "Language to display language names in. Default: en.", "default": "en"}
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

    async with httpx.AsyncClient(timeout=20) as client:
        if name == "translate_text":
            result = await _translate(client, arguments)
        elif name == "detect_language":
            result = await _detect(client, arguments)
        elif name == "translate_batch":
            result = await _translate_batch(client, arguments)
        elif name == "list_languages":
            result = await _list_langs(client, arguments)
        else:
            raise ValueError(f"Unknown tool: {name}")
    return [TextContent(type="text", text=result)]


async def _translate(client, args) -> str:
    params = {
        "q": args.get("text", ""),
        "target": args.get("target_language", "en"),
        "format": "text",
        "key": GOOGLE_TRANSLATE_API_KEY
    }
    if args.get("source_language"):
        params["source"] = args["source_language"]
    r = await client.post(f"{TRANSLATE_BASE}", params={"key": GOOGLE_TRANSLATE_API_KEY}, json={
        "q": args.get("text", ""),
        "target": args.get("target_language", "en"),
        "format": "text",
        **({"source": args["source_language"]} if args.get("source_language") else {})
    })
    if r.status_code != 200:
        return f"Translation error {r.status_code}: {r.text[:500]}"
    data = r.json()
    translations = data.get("data", {}).get("translations", [])
    if not translations:
        return "No translation returned."
    t = translations[0]
    detected = t.get("detectedSourceLanguage", "")
    src_note = f" (detected: {detected})" if detected else ""
    return (
        f"**Translation to '{args.get('target_language')}'**{src_note}:\n\n"
        f"{t.get('translatedText', '')}"
    )


async def _detect(client, args) -> str:
    r = await client.post(
        f"{TRANSLATE_BASE}/detect",
        params={"key": GOOGLE_TRANSLATE_API_KEY},
        json={"q": args.get("text", "")}
    )
    if r.status_code != 200:
        return f"Detection error {r.status_code}: {r.text[:500]}"
    detections = r.json().get("data", {}).get("detections", [[]])
    if not detections or not detections[0]:
        return "Could not detect language."
    d = detections[0][0]
    confidence = round(d.get("confidence", 0) * 100)
    return (
        f"**Detected language:** `{d.get('language','?')}`\n"
        f"Confidence: {confidence}%\n"
        f"Reliable: {'Yes' if d.get('isReliable') else 'No'}"
    )


async def _translate_batch(client, args) -> str:
    texts = args.get("texts", [])
    if not texts:
        return "No texts provided."
    payload = {
        "q": texts,
        "target": args.get("target_language", "en"),
        "format": "text"
    }
    if args.get("source_language"):
        payload["source"] = args["source_language"]
    r = await client.post(
        TRANSLATE_BASE,
        params={"key": GOOGLE_TRANSLATE_API_KEY},
        json=payload
    )
    if r.status_code != 200:
        return f"Batch translation error {r.status_code}: {r.text[:500]}"
    translations = r.json().get("data", {}).get("translations", [])
    lines = [f"**Batch translation to '{args.get('target_language')}':**\n"]
    for i, (orig, t) in enumerate(zip(texts, translations), 1):
        lines.append(f"{i}. **Original:** {orig[:100]}\n   **Translated:** {t.get('translatedText','')}\n")
    return "\n".join(lines)


async def _list_langs(client, args) -> str:
    r = await client.get(
        f"{TRANSLATE_BASE}/languages",
        params={"key": GOOGLE_TRANSLATE_API_KEY, "target": args.get("display_language", "en")}
    )
    if r.status_code != 200:
        return f"Error {r.status_code}: {r.text[:300]}"
    langs = r.json().get("data", {}).get("languages", [])
    lines = [f"**Supported Languages ({len(langs)} total):**\n"]
    for lang in langs:
        lines.append(f"`{lang.get('language','?')}` — {lang.get('name','?')}")
    return "\n".join(lines)


async def handle_sse(request: Request):
    async with sse_transport.connect_sse(
        request.scope, request.receive, request._send
    ) as streams:
        await server.run(streams[0], streams[1], server.create_initialization_options())
    return Response()


async def health(request: Request):
    return JSONResponse({"status": "ok", "service": "google-translate-mcp"})


app = Starlette(
    routes=[
        Route("/health", health),
        Route("/sse", handle_sse),
        Mount("/messages/", app=sse_transport.handle_post_message),
    ]
)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
