"""YouTube Transcript MCP Server — remote HTTP/SSE endpoint for claude.ai
Endpoint: GET /sse

Based on: github.com/kimtaeyoon83/mcp-server-youtube-transcript

Tools:
  get_transcript(video_id, lang)   — Fetch full transcript for a YouTube video
  list_transcripts(video_id)       — List available caption languages

No YouTube Data API key required. Uses youtube-transcript-api to fetch
publicly available captions directly from YouTube.
"""
import os
import re
from youtube_transcript_api import (
    YouTubeTranscriptApi,
    TranscriptsDisabled,
    NoTranscriptFound,
)
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.types import Tool, TextContent
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route, Mount
import uvicorn

PORT = int(os.environ.get("PORT", 8000))

server = Server("youtube-mcp")
sse_transport = SseServerTransport("/messages/")


def _extract_video_id(video_id_or_url: str) -> str:
    """Extract 11-char video ID from any YouTube URL format, or return as-is."""
    patterns = [
        r"(?:v=|youtu\.be/|embed/|shorts/|live/)([A-Za-z0-9_-]{11})",
        r"^([A-Za-z0-9_-]{11})$",
    ]
    s = video_id_or_url.strip()
    for pattern in patterns:
        m = re.search(pattern, s)
        if m:
            return m.group(1)
    return s


# ── Tool registry ─────────────────────────────────────────────────────────────

@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="get_transcript",
            description=(
                "Fetch the full transcript (captions/subtitles) of any YouTube video. "
                "Accepts a video ID (e.g. 'dQw4w9WgXcQ') or any YouTube URL. "
                "Returns timestamped text. No API key needed."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "video_id": {
                        "type": "string",
                        "description": (
                            "YouTube video ID or URL. Examples: "
                            "'dQw4w9WgXcQ', "
                            "'https://www.youtube.com/watch?v=dQw4w9WgXcQ', "
                            "'https://youtu.be/dQw4w9WgXcQ'"
                        ),
                    },
                    "lang": {
                        "type": "string",
                        "description": (
                            "BCP-47 language code for the transcript, e.g. 'en', 'hi', 'fr', 'de'. "
                            "Defaults to 'en'. Use list_transcripts to see available languages."
                        ),
                        "default": "en",
                    },
                },
                "required": ["video_id"],
            },
        ),
        Tool(
            name="list_transcripts",
            description=(
                "List all available transcript languages for a YouTube video. "
                "Shows both manual captions and auto-generated subtitles. "
                "Use this first when you're unsure which languages are available."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "video_id": {
                        "type": "string",
                        "description": "YouTube video ID or URL",
                    }
                },
                "required": ["video_id"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "get_transcript":
        result = _get_transcript(
            arguments["video_id"],
            arguments.get("lang", "en"),
        )
    elif name == "list_transcripts":
        result = _list_transcripts(arguments["video_id"])
    else:
        raise ValueError(f"Unknown tool: {name}")
    return [TextContent(type="text", text=result)]


# ── Implementations ───────────────────────────────────────────────────────────

def _get_entry_field(entry, field: str, default):
    """Safely read a field from transcript entry (dict or object)."""
    if isinstance(entry, dict):
        return entry.get(field, default)
    return getattr(entry, field, default)


def _get_transcript(video_id_or_url: str, lang: str = "en") -> str:
    vid = _extract_video_id(video_id_or_url)
    try:
        transcript_list = YouTubeTranscriptApi.list_transcripts(vid)

        try:
            transcript = transcript_list.find_transcript([lang])
            used_lang = lang
        except NoTranscriptFound:
            available = list(transcript_list)
            if not available:
                return f"No transcripts available for video: {vid}"
            transcript = available[0]
            used_lang = transcript.language_code

        entries = transcript.fetch()

        lines = [
            f"Transcript for https://youtu.be/{vid} (language: {used_lang})",
            "",
        ]
        for entry in entries:
            start = float(_get_entry_field(entry, "start", 0))
            text = str(_get_entry_field(entry, "text", "")).strip()
            mm = int(start // 60)
            ss = int(start % 60)
            lines.append(f"[{mm:02d}:{ss:02d}] {text}")

        return "\n".join(lines)

    except TranscriptsDisabled:
        return (
            f"Transcripts are disabled for video '{vid}'.\n"
            "The video owner has turned off captions/subtitles."
        )
    except NoTranscriptFound:
        return (
            f"No '{lang}' transcript found for '{vid}'.\n"
            "Use list_transcripts to see available languages."
        )
    except Exception as e:
        return f"Error fetching transcript for '{vid}': {e}"


def _list_transcripts(video_id_or_url: str) -> str:
    vid = _extract_video_id(video_id_or_url)
    try:
        transcript_list = YouTubeTranscriptApi.list_transcripts(vid)

        manual = []
        generated = []
        for t in transcript_list:
            entry = f"  {t.language_code:<8} {t.language}"
            if t.is_generated:
                generated.append(entry + "  (auto-generated)")
            else:
                manual.append(entry)

        lines = [f"Available transcripts for https://youtu.be/{vid}", ""]

        if manual:
            lines.append("Manual captions:")
            lines.extend(manual)
        if generated:
            if manual:
                lines.append("")
            lines.append("Auto-generated:")
            lines.extend(generated)
        if not manual and not generated:
            lines.append("No transcripts available.")

        return "\n".join(lines)

    except TranscriptsDisabled:
        return f"Transcripts are disabled for video '{vid}'."
    except Exception as e:
        return f"Error listing transcripts for '{vid}': {e}"


# ── HTTP app ──────────────────────────────────────────────────────────────────

async def handle_sse(request: Request):
    async with sse_transport.connect_sse(
        request.scope, request.receive, request._send
    ) as streams:
        await server.run(
            streams[0], streams[1], server.create_initialization_options()
        )


async def health(request: Request):
    return JSONResponse({"status": "ok", "service": "youtube-mcp"})


app = Starlette(
    routes=[
        Route("/health", health),
        Route("/sse", handle_sse),
        Route("/messages/", sse_transport.handle_post_message),
    ]
)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
