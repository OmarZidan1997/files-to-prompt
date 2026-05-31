"""One-shot: fetch the June 1 turnover briefing via the gateway and write a
readable, ASCII-clean markdown list."""
import asyncio
import json
import os
import unicodedata
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

CFG = Path("/home/user/.config/hospitable")
OUT = Path(__file__).resolve().parent.parent / "turnover_2026-06-01.md"


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


async def get_briefing(day):
    env = dict(os.environ)
    env["HOSPITABLE_MCP_TOKEN"] = (CFG / "mcp_token").read_text().strip()
    env["HOSPITABLE_ACCESS_TOKEN"] = (CFG / "pat").read_text().strip()
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
    L = ["# Turnover List - Sunday, 1 June 2026", ""]
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
    d = asyncio.run(get_briefing("2026-06-01"))
    OUT.write_text(fmt(d), encoding="utf-8")
    print("wrote", OUT, "-", len(d["turnovers"]), "turnovers")


if __name__ == "__main__":
    main()
