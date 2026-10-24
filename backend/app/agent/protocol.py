"""MCP over JSON-RPC 2.0 — the tools-only server surface: ``initialize``, ``ping``, ``tools/list``,
``tools/call`` (plus the client's notifications, which need no answer). Transport-agnostic: the HTTP layer
(``server``) hands in parsed messages and sends back what this returns.

Permissions are checked here, per call, against the config the listener holds — a refused call is a tool
error that names the switch to turn on. Every call lands in the activity feed (reads folded), so the user
sees what left the vault.
"""
import json
import logging
from dataclasses import dataclass

from starlette.concurrency import run_in_threadpool

from app import __version__
from app.agent import activity, tools
from app.agent.config import AgentConfig
from app.settings import Settings

log = logging.getLogger(__name__)

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")  # newest first; we answer in the client's if known

PERMISSION_LABELS = {  # as the switches read in Settings → Agent access
    "read": "Browse the Library and works",
}

INSTRUCTIONS = (
    "novelai-desktop is the user's NovelAI image studio. The Library holds reusable prompt blocks "
    "(category + name + tag text, positive or negative); works hold generated images, their recipes and saved "
    "block combinations. What you may do is set by the user in Settings → Agent access — a refused call names "
    "the permission that is off."
)


@dataclass(frozen=True)
class Context:
    settings: Settings
    config: AgentConfig


def _result(msg_id: object, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id: object, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def invalid_request(message: str = "Invalid request.") -> dict:
    return _error(None, -32600, message)


def _text(text: str, *, error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": error}


def _describe(tool: tools.Tool) -> dict:
    return {
        "name": tool.name, "title": tool.title, "description": tool.description, "inputSchema": tool.schema,
        "annotations": {"title": tool.title, "readOnlyHint": tool.permission == "read"},
    }


def _summary(args: dict) -> str:
    """What a call was about, for the activity feed — the arguments that name things, never their bulk."""
    parts = [f"{k.replace('_', ' ')} “{v[:60]}”" for k in ("query", "work_id", "image_id", "category")
             if isinstance(v := args.get(k), str) and v]
    parts += [f"{len(v)} {k}" for k in ("ids", "categories", "tags") if isinstance(v := args.get(k), list) and v]
    return ", ".join(parts)


async def handle(msg: object, ctx: Context) -> dict | None:
    """Answer one JSON-RPC message; None for notifications and responses (nothing to send back)."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
        return invalid_request()
    method = msg.get("method")
    if method is None and ("result" in msg or "error" in msg):
        return None  # a response to a request we never send
    if not isinstance(method, str):
        return _error(msg.get("id"), -32600, "Invalid request: no method.")
    if "id" not in msg:
        return None  # a notification (initialized, cancelled, …)
    msg_id = msg["id"]
    params = msg.get("params") or {}
    if not isinstance(params, dict):
        return _error(msg_id, -32602, "params must be an object.")

    if method == "initialize":
        asked = params.get("protocolVersion")
        return _result(msg_id, {
            "protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "novelai-desktop", "title": "NovelAI Desktop", "version": __version__},
            "instructions": INSTRUCTIONS,
        })
    if method == "ping":
        return _result(msg_id, {})
    if method == "tools/list":
        return _result(msg_id, {"tools": [_describe(t) for t in tools.TOOLS.values()]})
    if method == "tools/call":
        return await _call(msg_id, params, ctx)
    return _error(msg_id, -32601, f"Method not found: {method}")


async def _call(msg_id: object, params: dict, ctx: Context) -> dict:
    name = params.get("name")
    tool = tools.TOOLS.get(name) if isinstance(name, str) else None
    if tool is None:
        return _error(msg_id, -32602, f"Unknown tool: {name}")
    args = params.get("arguments") or {}
    if not isinstance(args, dict):
        return _error(msg_id, -32602, "arguments must be an object.")

    if not getattr(ctx.config.permissions, tool.permission):
        label = PERMISSION_LABELS[tool.permission]
        activity.record(tool.title, f"“{label}” is off", "denied")
        return _result(msg_id, _text(
            f"Refused: “{label}” is turned off in Settings → Agent access. Ask the user to allow it.", error=True))

    try:
        out = await run_in_threadpool(tools.run, tool, ctx.settings, args)
    except tools.ToolError as exc:
        return _result(msg_id, _text(str(exc), error=True))
    except Exception:  # noqa: BLE001 — an agent call must never take the listener down
        log.exception("Agent tool %s failed", tool.name)
        activity.record(tool.title, "see the app log", "error")
        return _result(msg_id, _text("Internal error — see the app log.", error=True))

    activity.record_read(tool.title, _summary(args))
    if isinstance(out, tools.ImageResult):
        return _result(msg_id, {"content": [tools.image_block(out)], "isError": False})
    return _result(msg_id, _text(json.dumps(out, ensure_ascii=False)))
