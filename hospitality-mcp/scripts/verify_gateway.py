#!/usr/bin/env python3
"""Self-check for the Hospitable gateway: starts it, lists tools, and calls a
couple of them against the live account so you can confirm it actually works.

Usage:
    HOSPITABLE_MCP_TOKEN=...  HOSPITABLE_ACCESS_TOKEN=...  python scripts/verify_gateway.py [day]

If those env vars are unset it falls back to reading them from
~/.config/hospitable/{mcp_token,pat} (handy for local dev).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def _load_token(env_name: str, filename: str) -> str | None:
    val = os.environ.get(env_name)
    if val:
        return val.strip()
    for base in (Path.home() / ".config" / "hospitable", Path("/home/user/.config/hospitable")):
        path = base / filename
        if path.exists():
            return path.read_text().strip()
    return None


async def main(day: str) -> int:
    mcp_token = _load_token("HOSPITABLE_MCP_TOKEN", "mcp_token")
    pat = _load_token("HOSPITABLE_ACCESS_TOKEN", "pat")
    if not mcp_token:
        print("✗ No HOSPITABLE_MCP_TOKEN (mcp:use bearer). Cannot start the gateway.")
        return 1

    env = dict(os.environ)
    env["HOSPITABLE_MCP_TOKEN"] = mcp_token
    if pat:
        env["HOSPITABLE_ACCESS_TOKEN"] = pat
    env.setdefault("PYTHONPATH", "src")

    params = StdioServerParameters(
        command=sys.executable, args=["-m", "hospitality_mcp.gateway"], env=env
    )
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = (await s.list_tools()).tools
            names = [t.name for t in tools]
            custom = [n for n in names if "turnover" in n or n.startswith("custom_")]
            print(f"✓ Gateway started — {len(names)} tools exposed")
            print(f"  upstream (Hospitable): {len(names) - len(custom)}  |  custom: {custom}")

            print("\n✓ Passthrough check: calling native tool 'get-user' ...")
            res = await s.call_tool("get-user", {})
            print("  isError:", res.isError, "| bytes:", len(res.content[0].text) if res.content else 0)

            if pat:
                print(f"\n✓ Custom check: daily_turnover_briefing(day={day!r}) ...")
                res = await s.call_tool("daily_turnover_briefing", {"day": day})
                if res.isError:
                    print("  ✗ ERROR:", res.content[0].text[:400])
                    return 1
                d = json.loads(res.content[0].text)
                print("  date:", d["date"], "| summary:", json.dumps(d["summary"]))
                for a in d["alerts"][:6]:
                    print("   -", a)
            else:
                print("\n(skipping custom tool: no HOSPITABLE_ACCESS_TOKEN / PAT)")

    print("\nALL GOOD ✅")
    return 0


if __name__ == "__main__":
    day = sys.argv[1] if len(sys.argv) > 1 else "tomorrow"
    raise SystemExit(asyncio.run(main(day)))
