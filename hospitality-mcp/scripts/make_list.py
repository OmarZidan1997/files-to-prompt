"""One-shot: fetch a turnover briefing via the gateway and write a readable,
ASCII-clean markdown list.

Usage:
    python scripts/make_list.py            # tomorrow (default)
    python scripts/make_list.py 2026-06-12 # a specific day (ISO date, weekday,
                                           # 'today', 'tomorrow', or an offset like '+2')
"""
import asyncio
import json
import os
import sys
import unicodedata
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

CFG = Path.home() / ".config" / "hospitable"
ROOT = Path(__file__).resolve().parent.parent


def aa(s: str) -> str:
    """Transliterate to plain ASCII so any terminal renders it cleanly."""
    for k, v in {
        "“": '"', "”": '"', "‘": "'", "’": "'",
        "—": "-", "–": "-", "·": "-", "…": "...",
    }.items():
        s = s.replace(k, v)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.encode("ascii", "replace").decode("ascii")


def _cred(env_name, fname):
    """Prefer an already-set env var (e.g. from .env); fall back to the config file."""
    val = os.environ.get(env_name)
    if val:
        return val
    return (CFG / fname).read_text().strip()


async def get_briefing(day):
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
    except ImportError:
        pass
    env = dict(os.environ)
    env["HOSPITABLE_MCP_TOKEN"] = _cred("HOSPITABLE_MCP_TOKEN", "mcp_token")
    env["HOSPITABLE_ACCESS_TOKEN"] = _cred("HOSPITABLE_ACCESS_TOKEN", "pat")
    env["PYTHONPATH"] = "src"
    params = StdioServerParameters(command="python", args=["-m", "hospitality_mcp.gateway"], env=env)
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            res = await s.call_tool("daily_turnover_briefing", {"day": day})
            return json.loads(res.content[0].text)


def fmt(d):
    conv = {c["reservation_id"]: c for c in d["guest_conversations"]}

    def msgs_for(t, role):
        rid = t["check_out_reservation_id"] if role == "departing" else t["check_in_reservation_id"]
        c = conv.get(rid)
        if c:
            return c["messages"]
        for c in d["guest_conversations"]:
            if c["property"] == t["property_name"] and c["role"] == role:
                return c["messages"]
        return []

    sm = d["summary"]
    try:
        from datetime import date as _date

        title_date = _date.fromisoformat(str(d["date"])).strftime("%A, %-d %B %Y")
    except Exception:
        title_date = str(d.get("date", ""))
    L = [f"# Turnover List - {title_date}", ""]
    L.append(f"{sm['turnovers']} turnovers | {sm['same_day_turnarounds']} same-day turnaround | "
             f"{sm['check_outs']} check-outs | {sm['check_ins']} check-ins | "
             f"{sm['open_complaints']} complaints | {sm['luggage_holds']} luggage requests")
    L.append("")
    if d["alerts"]:
        L.append("## Priority alerts")
        for a in d["alerts"]:
            L.append(f"- {aa(a)}")
        L.append("")
    L.append("## Turnovers")
    L.append("")
    for i, t in enumerate(d["turnovers"], 1):
        if t["same_day_turnaround"]:
            typ = "[SAME-DAY TURNAROUND - clean between checkout & check-in]"
        elif t["check_out_reservation_id"] and not t["check_in_reservation_id"]:
            typ = "[CHECKOUT - clean after departure]"
        else:
            typ = "[CHECK-IN - prep for arrival]"
        L.append(f"### {i}. {aa(t['property_name'])}  {typ}")
        flags = [n for n in t["notes"] if "Party of" not in n and "genuine message" not in n]
        for n in flags:
            L.append(f"- (!) {aa(n)}")
        dep = msgs_for(t, "departing")
        arr = msgs_for(t, "arriving")
        if dep:
            L.append("- Departing guest said:")
            for m in dep:
                L.append(f'    - "{aa(" ".join(m.split()))}"')
        if arr:
            L.append("- Arriving guest said:")
            for m in arr:
                L.append(f'    - "{aa(" ".join(m.split()))}"')
        if not flags and not dep and not arr:
            L.append("- Nothing flagged. Standard turnover.")
        L.append("")
    return "\n".join(L)


def main():
    day = sys.argv[1] if len(sys.argv) > 1 else "tomorrow"
    d = asyncio.run(get_briefing(day))
    out = ROOT / f"turnover_{d['date']}.md"
    out.write_text(fmt(d), encoding="utf-8")
    print("wrote", out, "-", len(d["turnovers"]), "turnovers")


if __name__ == "__main__":
    main()
