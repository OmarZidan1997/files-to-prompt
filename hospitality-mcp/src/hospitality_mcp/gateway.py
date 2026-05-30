"""Hospitable MCP **gateway**.

This is the "super-perfect" server: it connects to Hospitable's own hosted MCP
server (https://mcp.hospitable.com/mcp), re-exposes every one of its tools
unchanged, AND adds our custom composite tools on top — most importantly a
one-shot ``daily_turnover_briefing`` that Hospitable's native tools don't
provide.

Architecture::

    AI assistant ──stdio──▶  this gateway  ──Streamable HTTP──▶  Hospitable MCP
                                  │                                 (59 tools)
                                  └─ custom tools ──Public API v2──▶ Hospitable

Two credentials (kept out of the repo — pass via env):
    HOSPITABLE_MCP_TOKEN     Bearer token with the ``mcp:use`` scope
                             (Hospitable → AI agents → Fallback bearer tokens)
    HOSPITABLE_ACCESS_TOKEN  Personal Access Token (pat:read/pat:write) used by
                             the custom tools' direct Public API v2 calls
Optional:
    HOSPITABLE_MCP_URL       default https://mcp.hospitable.com/mcp

Run:  python -m hospitality_mcp.gateway
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any, Dict, List, Optional, Sequence

import mcp.types as types
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from . import briefing
from .hospitable import HospitableClient

DEFAULT_MCP_URL = "https://mcp.hospitable.com/mcp"

_DAY_SCHEMA = {
    "type": "object",
    "properties": {
        "day": {
            "type": "string",
            "description": "Which day: 'today', 'tomorrow', a weekday name, an "
            "ISO date (2026-05-31), or an offset like '+2'. Defaults to tomorrow.",
        }
    },
    "required": [],
    "additionalProperties": False,
}

# Custom tools this gateway adds on top of Hospitable's native MCP tools.
CUSTOM_TOOLS: List[types.Tool] = [
    types.Tool(
        name="daily_turnover_briefing",
        description=(
            "Custom: a single operational briefing for a day across ALL properties — "
            "turnovers (with same-day turnarounds flagged), check-ins, check-outs, "
            "open issues/complaints (from reservation issue alerts), luggage holds, "
            "and prioritized alerts. Best tool for 'what are the turnovers tomorrow "
            "and the important notes I need to know'."
        ),
        inputSchema=_DAY_SCHEMA,
    ),
    types.Tool(
        name="list_turnovers",
        description=(
            "Custom: property turnovers for a day, sorted by priority, each with "
            "check-in/check-out reservations, same-day-turnaround flag and notes."
        ),
        inputSchema=_DAY_SCHEMA,
    ),
]
# Maps each custom tool name to the briefing function that implements it.
CUSTOM_HANDLERS = {
    "daily_turnover_briefing": briefing.daily_briefing,
    "list_turnovers": briefing.list_turnovers,
}


def _json_block(payload: Any) -> List[types.TextContent]:
    return [types.TextContent(type="text", text=json.dumps(payload, indent=2, default=str))]


def build_custom_tools(upstream_names):
    """Return (exposed_tools, dispatch) for the custom tools.

    Any custom tool whose name collides with an upstream Hospitable tool is
    renamed with a ``custom_`` prefix so nothing upstream is shadowed.
    ``dispatch`` maps the exposed name back to the original custom-tool name.
    """
    exposed: List[types.Tool] = []
    dispatch: Dict[str, str] = {}
    for tool in CUSTOM_TOOLS:
        name = tool.name if tool.name not in upstream_names else f"custom_{tool.name}"
        exposed.append(tool.model_copy(update={"name": name}))
        dispatch[name] = tool.name
    return exposed, dispatch


async def _serve(mcp_url: str, mcp_token: str, pat: Optional[str]) -> None:
    headers = {"Authorization": f"Bearer {mcp_token}"}

    # Keep one upstream session open for the gateway's whole lifetime.
    async with streamablehttp_client(mcp_url, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as upstream:
            init = await upstream.initialize()
            upstream_server_name = getattr(init.serverInfo, "name", "Hospitable MCP")
            upstream_tools = (await upstream.list_tools()).tools
            upstream_names = {t.name for t in upstream_tools}

            # Custom tools, renamed if they would clash with an upstream tool.
            exposed_custom, custom_dispatch = build_custom_tools(upstream_names)

            pat_client = HospitableClient(token=pat) if pat else None

            server: Server = Server("hospitable-gateway")

            @server.list_tools()
            async def list_tools() -> List[types.Tool]:
                return list(upstream_tools) + exposed_custom

            @server.call_tool()
            async def call_tool(name: str, arguments: Dict[str, Any]) -> Sequence[types.ContentBlock]:
                arguments = arguments or {}

                # --- custom composite tools (direct Public API v2) ---------
                if name in custom_dispatch:
                    if pat_client is None:
                        raise ValueError(
                            "This custom tool needs HOSPITABLE_ACCESS_TOKEN "
                            "(a Personal Access Token) to be set."
                        )
                    fn = CUSTOM_HANDLERS[custom_dispatch[name]]
                    day = arguments.get("day", "tomorrow")
                    payload = await asyncio.to_thread(fn, pat_client, day)
                    return _json_block(payload)

                # --- everything else: passthrough to Hospitable's MCP ------
                if name in upstream_names:
                    result = await upstream.call_tool(name, arguments)
                    return list(result.content)

                raise ValueError(f"Unknown tool: {name!r}")

            init_opts = server.create_initialization_options()
            async with stdio_server() as (r, w):
                # Helpful startup line on stderr (stdout is the MCP channel).
                print(
                    f"[hospitable-gateway] proxying {len(upstream_tools)} tools from "
                    f"{upstream_server_name} + {len(exposed_custom)} custom "
                    f"({', '.join(custom_dispatch)})"
                    + ("" if pat_client else "  [custom tools disabled: no PAT]"),
                    file=sys.stderr,
                    flush=True,
                )
                await server.run(r, w, init_opts)


def main() -> None:
    mcp_url = os.environ.get("HOSPITABLE_MCP_URL", DEFAULT_MCP_URL)
    mcp_token = os.environ.get("HOSPITABLE_MCP_TOKEN")
    pat = os.environ.get("HOSPITABLE_ACCESS_TOKEN")
    if not mcp_token:
        raise SystemExit(
            "HOSPITABLE_MCP_TOKEN is required (a Hospitable bearer token with the "
            "'mcp:use' scope). Set HOSPITABLE_ACCESS_TOKEN too to enable the custom tools."
        )
    asyncio.run(_serve(mcp_url, mcp_token, pat))


if __name__ == "__main__":
    main()
