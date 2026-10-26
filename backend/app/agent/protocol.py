"""MCP over JSON-RPC 2.0 — the tools-only server surface: ``initialize``, ``ping``, ``tools/list``,
``tools/call`` (plus the client's notifications, which need no answer). Transport-agnostic: the HTTP layer
(``server``) hands in parsed messages and sends back what this returns.

Permissions are checked here, per call, against the config the listener holds — a refused call is a tool
error that names the switch to turn on. Every call lands in the activity feed (reads folded), so the user
sees what left the vault; every write is also announced to the open app (``changes``).
"""
import json
import logging
from dataclasses import dataclass

import anyio

from app import __version__
from app.agent import activity, changes, errors, tools
from app.agent.config import AgentConfig
from app.settings import Settings

log = logging.getLogger(__name__)

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")  # newest first; we answer in the client's if known

# Agent calls run on a few worker threads of their own: a burst of parallel calls (waiting on each other's vault
# locks) never takes the shared pool the app's own requests — saves included — run on.
_THREADS = anyio.CapacityLimiter(4)

PERMISSION_LABELS = {  # as the switches read in Settings → Agents
    "read": "Browse the Library and works",
    "blocks_write": "Create and edit blocks",
    "blocks_delete": "Delete blocks",
    "works_write": "Create and edit works",
    "combos_write": "Create and edit combinations",
    "works_delete": "Delete images and combinations",
    "generate": "Generate images",
}

INSTRUCTIONS = (
    "novelai-desktop is the user's NovelAI image studio. The Library holds reusable prompt blocks "
    "(category + name + tag text, positive or negative); works hold generated images (with their recipes, arranged "
    "in a gallery of sections and albums) and saved block combinations (ready block sets for the generation "
    "station). What you may do is set by the user in "
    "Settings → Agents — a refused call names the permission that is off. Whatever you change shows up in "
    "the open app at once, and the user sees a log of it. Generating costs the user's Anlas unless it is free: "
    "check estimate_cost, and never pass paid: true without the user's say-so."
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
    annotations = {"title": tool.title, "readOnlyHint": tool.permission == "read"}
    if tool.permission != "read":
        annotations["destructiveHint"] = tool.permission.endswith("_delete")
    if tool.name in tools.REACHES_NOVELAI:
        annotations["openWorldHint"] = True  # talks to NovelAI (a generation may spend Anlas)
    return {"name": tool.name, "title": tool.title, "description": tool.description, "inputSchema": tool.schema,
            "annotations": annotations}


_KINDS: dict[str, activity.Kind] = {"read": "read", "generate": "generate"}  # every other permission writes


def _kind(tool: tools.Tool) -> activity.Kind:
    return _KINDS.get(tool.permission, "write")


def _summary(args: dict) -> str:
    """What a call was about, for the activity feed — the arguments that name things, never their bulk."""
    parts = [f"{k.replace('_', ' ')} “{v[:60]}”" for k in ("query", "work_id", "image_id", "category")
             if isinstance(v := args.get(k), str) and v]
    parts += [f"{len(v)} {k.replace('_', ' ')}" for k in ("ids", "image_ids", "categories", "tags")
              if isinstance(v := args.get(k), list) and v]
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

    kind = _kind(tool)
    if not getattr(ctx.config.permissions, tool.permission):
        label = PERMISSION_LABELS[tool.permission]
        activity.record(tool.title, f"“{label}” is off", "denied", kind=kind)
        return _result(msg_id, _text(
            f"Refused: “{label}” is turned off in Settings → Agents. Ask the user to allow it.", error=True))

    try:
        out = await anyio.to_thread.run_sync(tools.run, tool, ctx.settings, args, limiter=_THREADS)
    except errors.Refused as exc:  # over the user's limits — shown like a switched-off permission
        activity.record(tool.title, exc.detail, "denied", kind=kind)
        return _result(msg_id, _text(f"Refused: {exc}", error=True))
    except errors.GenerationFailed as exc:  # the request went out: the prompt left the machine, Anlas may be gone
        activity.record(tool.title, str(exc), "error", kind=kind, cost=exc.cost or None)
        changes.publish({"kind": "failed", "work_id": None, "cost": exc.cost or None,
                         "message": f"Agent generation failed — {exc}"})
        return _result(msg_id, _text(str(exc), error=True))
    except errors.ToolError as exc:
        return _result(msg_id, _text(str(exc), error=True))
    except Exception:  # noqa: BLE001 — an agent call must never take the listener down
        log.exception("Agent tool %s failed", tool.name)
        activity.record(tool.title, "see the app log", "error", kind=kind)
        return _result(msg_id, _text("Internal error — see the app log.", error=True))

    if isinstance(out, tools.Written):
        activity.record(out.action, out.detail, "ok", kind=kind, cost=out.cost)
        price = "" if out.cost is None else " · free" if out.cost == 0 else f" · ◆ {out.cost}"
        changes.publish({"kind": "work" if out.work_id else "library", "work_id": out.work_id, "cost": out.cost,
                         "message": f"Agent {out.action[0].lower()}{out.action[1:]} — {out.detail}{price}"})
        out = out.result
    else:  # a read, or a write that turned out to change nothing (all duplicates, nothing found)
        activity.record_read(tool.title, _summary(args))
    if isinstance(out, tools.ImageResult):
        caption = [{"type": "text", "text": out.caption}] if out.caption else []
        return _result(msg_id, {"content": [tools.image_block(out), *caption], "isError": False})
    return _result(msg_id, _text(json.dumps(out, ensure_ascii=False)))
