"""The LLM layer: Claude drives the turnover briefing **and** Hospitable's full
read-only toolset, picking whatever fits the staff member's request.

Tools available to the model:
  - get_turnover_briefing  (custom: turnover prep sheet + full guest conversations)
  - every read-only Hospitable tool + create-task, via mcp_bridge

If a request needs something none of these tools cover (or a write/guest-facing
action that isn't exposed), the model is told to say so plainly rather than guess.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import date
from typing import Any, AsyncIterator, Dict, List, Optional

import anthropic

from . import mcp_bridge
from .. import briefing
from ..hospitable import HospitableClient
import os

MODEL = "claude-opus-4-8"

SYSTEM_PROMPT = """\
You are the operations assistant for a short-term rental / vacation-rental \
business. Your users are in-house and cleaning staff and operations managers. \
Answer their questions and prep their work using the tools available to you.

Tools you have:
- get_turnover_briefing: the best tool for "what are the turnovers / check-ins / \
  check-outs and what do we need to know" for a day. It flags SAME-DAY \
  TURNAROUNDS, excludes cancelled reservations, and returns the full genuine \
  guest conversation for each turnover so you can extract heads-up notes.
- Hospitable's read-only tools: properties, reservations, reservation messages, \
  reviews, tasks, calendar, upsells, payouts/transactions, teammates, etc.
- create-task: create an operations task (e.g. a maintenance or cleaning task). \
  This is the only action that changes anything — use it ONLY when the user \
  explicitly asks, and tell them exactly what you created.

How to work:
- Use whatever tool fits the request; chain several if needed.
- Most Hospitable tools need a property UUID. Resolve names to UUIDs first with \
  get-properties (use per_page up to 100), then call the tool you need.
- If a request needs an action no tool supports — or a write / guest-facing \
  action you don't have (sending messages, responding to reviews, cancelling, \
  smart-locks) — say plainly that you can't do that and, if useful, suggest what \
  you CAN do instead. Never invent data or pretend an action happened.

Turnover prep: lead with a one-line summary, put SAME-DAY TURNAROUNDS first and \
clearly marked. Then give EACH property its own clearly separated block. The notes \
for every flat must be specific to THAT flat's situation — never generic, never \
copy-pasted between properties, and never a vague "check everything". Differentiate \
each block by:
- which party is leaving vs arriving (and party sizes, e.g. 7 out -> 5 in), \
- the timing pressure (same-day vs next-day, requested check-in/out times, \
  early-arrival or bag-drop requests with the actual time), \
- concrete heads-up items pulled from THIS reservation's conversation: \
  damage/maintenance to fix, restock/amenity requests (e.g. a missing hairdryer), \
  extra guests, luggage holds, complaints, or special requests. \
Ground every note in what the guest actually wrote and quote a short phrase; \
translate non-English quotes into English. Order each flat's notes by what staff \
must act on first. If a flat genuinely has nothing notable, say "Standard turnover \
— no special requests" rather than padding it. The goal: a cleaner reading one \
flat's block knows exactly what makes that flat different today.

Cleaner assignment: ALWAYS state who is cleaning each turnover, taken from the \
briefing's cleaner field / CLEANING note (it comes from the real Hospitable \
cleaning task). If a turnover has no cleaner (cleaner is null / "NO cleaner \
assigned"), flag it loudly — call it out per flat and in the summary, prioritising \
same-day turnarounds — because an unassigned clean is the thing most likely to be \
missed. Never claim a clean is unassigned when the cleaner field has a name, and \
never invent a cleaner. Use 🧹 for the cleaner line.

Reviews / feedback: do NOT just list every review. Read the comments, then \
AGGREGATE — group recurring issues, and for each report how many guests raised it \
and the relevant average/affected rating (e.g. "cleanliness: avg 7.5, 4 of 9 \
guests mention dust"). Then give a concrete, prioritized list of things to FIX or \
BUY. Reviews and messages may be in other languages — translate any quoted text \
into English.

Output format (keep it consistent every time):
1. One-line summary.
2. A scannable summary TABLE, one row per flat. Keep cells SHORT — a few words \
   each, no full sentences (the full actions go in the detail blocks). Long cells \
   make the line unreadable once it's copied to WhatsApp. Use NO emoji and NO \
   markdown links inside table cells. Don't put a raw "|" inside a cell (write \
   "Paddington 2nd Floor", not "Paddington | 2nd Floor"). Suggested columns: \
   # | Flat | Status | Party (out->in) | Top action (<= 4 words).
3. Per-flat detail blocks below the table, where the emojis live. Keep each note \
   to one short line; avoid markdown links — write a channel/source as plain text \
   (e.g. "Booking.com"), not as a [label](url).

Use this emoji legend consistently — same symbol, same meaning, every flat:
🔴 same-day turnaround (urgent)  ·  🟢 standard turnover  ·  🚪 check-out  ·  \
🔑 check-in  ·  🧹 cleaner assigned (or ⚠️ none)  ·  👥 party size  ·  \
⏰ timing / requested times  ·  🧳 luggage  ·  🛠️ maintenance/fix  ·  \
🧴 restock/amenity  ·  💬 guest said  ·  ✅ action needed.
Don't invent new emojis or use one symbol for two meanings.

Be concise and scannable — these are people on the ground.
"""

# Custom (local) tool. Everything else comes from mcp_bridge.tool_defs().
LOCAL_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "get_turnover_briefing",
        "description": (
            "Turnover briefing for a day across all properties: turnovers (with "
            "same-day turnarounds flagged), check-ins, check-outs, alerts, and the "
            "full genuine guest conversation per turnover reservation. Cancelled "
            "reservations excluded. Best for turnover/check-in prep and heads-up notes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "day": {
                    "type": "string",
                    "description": (
                        "Which day: 'today', 'tomorrow', a weekday name, an ISO date "
                        "(2026-06-12), or an offset like '+2'. Defaults to tomorrow."
                    ),
                }
            },
            "required": [],
            "additionalProperties": False,
        },
    }
]

_hosp_client: Optional[HospitableClient] = None


def _hospitable() -> HospitableClient:
    global _hosp_client
    if _hosp_client is None:
        token = os.environ.get("HOSPITABLE_ACCESS_TOKEN")
        if not token:
            raise RuntimeError("HOSPITABLE_ACCESS_TOKEN is not set.")
        _hosp_client = HospitableClient(token=token)
    return _hosp_client


async def _dispatch(name: str, args: Dict[str, Any], mcp) -> str:
    if name == "get_turnover_briefing":
        day = (args or {}).get("day") or "tomorrow"
        payload = await asyncio.to_thread(briefing.daily_briefing, _hospitable(), day)
        return json.dumps(payload, ensure_ascii=False, default=str)
    if mcp is None:
        return json.dumps({"error": f"tool {name} is not available right now."})
    try:
        return await mcp.call(name, args or {})
    except Exception as exc:
        return json.dumps({"error": f"tool {name} failed: {exc}"})


@asynccontextmanager
async def _session_or_none():
    """Yield a live Hospitable MCP session, or None if the bridge is unavailable."""
    cm = mcp_bridge.session()
    try:
        mcp = await cm.__aenter__()
    except Exception as exc:  # missing token / connect failure → custom tool only
        print(f"WARNING: Hospitable tool bridge unavailable: {exc}")
        yield None
        return
    try:
        yield mcp
    finally:
        await cm.__aexit__(None, None, None)


async def stream_reply(messages: List[Dict[str, Any]]) -> AsyncIterator[str]:
    """Yield the assistant's answer as text chunks for an SSE stream."""
    client = anthropic.AsyncAnthropic()
    system = [
        {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}},
        # Volatile, kept after the cached block so it doesn't break the cache.
        {"type": "text", "text": f"Today's date is {date.today().isoformat()}."},
    ]
    work: List[Dict[str, Any]] = list(messages)

    async with _session_or_none() as mcp:
        tools = list(LOCAL_TOOLS)
        if mcp is not None:
            try:
                tools += await mcp.tool_defs()
            except Exception as exc:
                print(f"WARNING: could not load Hospitable tools: {exc}")

        while True:
            async with client.messages.stream(
                model=MODEL,
                max_tokens=16000,
                system=system,
                thinking={"type": "adaptive"},
                output_config={"effort": "high"},
                tools=tools,
                messages=work,
            ) as stream:
                async for text in stream.text_stream:
                    yield text
                final = await stream.get_final_message()

            if final.stop_reason != "tool_use":
                break

            work.append({"role": "assistant", "content": final.content})
            results: List[Dict[str, Any]] = []
            for block in final.content:
                if block.type == "tool_use":
                    results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": await _dispatch(block.name, block.input, mcp),
                        }
                    )
            work.append({"role": "user", "content": results})
