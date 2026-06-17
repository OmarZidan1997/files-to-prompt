"""Bridge the web chatbot to Hospitable's full hosted MCP toolset.

The chatbot should be able to use *any* capability Hospitable exposes, not a
hand-picked few. For each chat request we open one short-lived session to
Hospitable's hosted MCP server (opened and closed within the same async task, so
anyio cancel scopes stay consistent), discover its tools, and expose them to
Claude as ordinary tools.

Scope is **read-only + safe**: a tool is exposed only if Hospitable marks it
``readOnlyHint`` (or it is the explicitly-allowed ``create-task``). Write /
guest-facing actions (send message, respond-to-review, cancel, smartlock, etc.)
are never exposed, so the bot simply has no tool for them — and tells the user.

Hospitable's MCP lazy-loads schemas: ``list_tools`` returns names + descriptions
only, so we resolve real input schemas (and the readOnlyHint annotation) via the
``get-tool-schema`` meta tool. The resolved tool list is static, so we cache it
across requests and only re-discover once per process.
"""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict, List, Optional

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

DEFAULT_MCP_URL = "https://mcp.hospitable.com/mcp"

# Always allow this one write tool (safe, non-guest-facing); never expose this meta tool.
_EXTRA_ALLOW = {"create-task"}
_EXCLUDE = {"get-tool-schema"}

# Resolved tool definitions are static for a process — discover once, reuse.
_defs_cache: Optional[List[Dict[str, Any]]] = None


def _text_of(result: Any) -> str:
    """Concatenate the text content blocks of an MCP tool result."""
    parts = []
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text is not None:
            parts.append(text)
    return "".join(parts)


class _Conn:
    """A live Hospitable MCP session, valid for the lifetime of one `session()`."""

    def __init__(self, session: ClientSession):
        self._session = session

    async def _fetch_schemas(self, names: List[str]) -> Dict[str, dict]:
        out: Dict[str, dict] = {}
        for i in range(0, len(names), 10):  # meta tool accepts up to 10 names per call
            result = await self._session.call_tool("get-tool-schema", {"tools": names[i : i + 10]})
            try:
                data = json.loads(_text_of(result)).get("data", [])
            except json.JSONDecodeError:
                continue
            for entry in data:
                if entry.get("status") == "ok":
                    out[entry["name"]] = entry
        return out

    async def tool_defs(self) -> List[Dict[str, Any]]:
        """Anthropic tool definitions for every exposed Hospitable tool (cached)."""
        global _defs_cache
        if _defs_cache is not None:
            return _defs_cache
        listed = (await self._session.list_tools()).tools
        names = [t.name for t in listed if t.name not in _EXCLUDE]
        schemas = await self._fetch_schemas(names)
        defs: List[Dict[str, Any]] = []
        for name, info in schemas.items():
            read_only = bool((info.get("annotations") or {}).get("readOnlyHint"))
            if not (read_only or name in _EXTRA_ALLOW):
                continue
            schema = info.get("inputSchema") or {"type": "object", "properties": {}}
            schema.setdefault("type", "object")
            defs.append(
                {"name": name, "description": info.get("description") or "", "input_schema": schema}
            )
        _defs_cache = defs
        return defs

    async def call(self, name: str, arguments: Dict[str, Any]) -> str:
        result = await self._session.call_tool(name, arguments or {})
        return _text_of(result)


@asynccontextmanager
async def session() -> AsyncIterator[_Conn]:
    """Open one Hospitable MCP session for the duration of a chat request."""
    token = os.environ.get("HOSPITABLE_MCP_TOKEN")
    if not token:
        raise RuntimeError("HOSPITABLE_MCP_TOKEN is not set — cannot reach Hospitable's tools.")
    url = os.environ.get("HOSPITABLE_MCP_URL", DEFAULT_MCP_URL)
    async with streamablehttp_client(url, headers={"Authorization": f"Bearer {token}"}) as (
        read,
        write,
        _,
    ):
        async with ClientSession(read, write) as s:
            await s.initialize()
            yield _Conn(s)
