"""The hospitality MCP server.

Exposes operational tools so an LLM can answer questions like:
  "Get me all the turnovers tomorrow and any important notes I need to know."
  "What's the daily briefing for today?"
  "Any open guest complaints?"
  "Who's checking in tomorrow and is anyone leaving luggage?"

Run it over stdio:
    python -m hospitality_mcp

Backend selection (env var ``HOSPITALITY_BACKEND``):
    mock (default) - bundled in-memory data
    <future>       - wire a real PMS client in ``build_client``
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP

from . import briefing
from .client import HospitalityClient, MockClient
from .dates import DateParseError, parse_day
from .models import ComplaintStatus, serialize

mcp = FastMCP("hospitality-mcp")


def build_client() -> HospitalityClient:
    backend = os.environ.get("HOSPITALITY_BACKEND", "mock").lower()
    if backend == "mock":
        return MockClient()
    if backend == "hospitable":
        token = os.environ.get("HOSPITABLE_ACCESS_TOKEN")
        if not token:
            raise ValueError(
                "HOSPITALITY_BACKEND=hospitable requires HOSPITABLE_ACCESS_TOKEN "
                "(a Hospitable Personal Access Token)."
            )
        from .hospitable import HospitableClient

        return HospitableClient(token=token)
    raise ValueError(
        f"Unknown HOSPITALITY_BACKEND={backend!r}. Use 'mock' or 'hospitable'."
    )


client: HospitalityClient = build_client()


def _resolve_day(day: Optional[str]) -> Any:
    try:
        return parse_day(day)
    except DateParseError as exc:
        raise ValueError(str(exc)) from exc


# --------------------------------------------------------------------------
# The headline tool: everything an operator needs for a given day.
# --------------------------------------------------------------------------
@mcp.tool()
def daily_briefing(day: str = "tomorrow") -> Dict[str, Any]:
    """Full operational briefing for a day: turnovers, check-ins, check-outs,
    open complaints, luggage holds, and prioritized alerts.

    `day` accepts 'today', 'tomorrow', a weekday, an ISO date (2026-05-31), or
    an offset like '+2'. This is the best tool for "what do I need to know
    about tomorrow's turnovers and guests".
    """
    return briefing.daily_briefing(client, day)


@mcp.tool()
def list_turnovers(day: str = "tomorrow") -> List[Dict[str, Any]]:
    """List property turnovers for a day, sorted by priority, each with the
    cleaning assignment and important notes (late checkout, early check-in,
    VIP, luggage, open issues). `day` accepts the same formats as daily_briefing.
    """
    return briefing.list_turnovers(client, day)


@mcp.tool()
def list_check_ins(day: str = "tomorrow") -> List[Dict[str, Any]]:
    """Reservations checking in on a day, with guest details and arrival notes."""
    d = _resolve_day(day)
    out = []
    for r in client.check_ins_on(d):
        guest = client.get_guest(r.guest_id)
        out.append({"reservation": r, "guest": guest, "property": client.get_property(r.property_id)})
    return serialize(out)


@mcp.tool()
def list_check_outs(day: str = "tomorrow") -> List[Dict[str, Any]]:
    """Reservations checking out on a day, with guest details and departure notes."""
    d = _resolve_day(day)
    out = []
    for r in client.check_outs_on(d):
        guest = client.get_guest(r.guest_id)
        out.append({"reservation": r, "guest": guest, "property": client.get_property(r.property_id)})
    return serialize(out)


@mcp.tool()
def list_complaints(status: str = "open") -> List[Dict[str, Any]]:
    """Guest complaints / issues, sorted by severity.

    `status` is one of: 'open', 'in_progress', 'resolved', or 'all'.
    """
    status = (status or "all").lower()
    if status == "all":
        items = client.complaints()
    else:
        try:
            items = client.complaints(status=ComplaintStatus(status))
        except ValueError:
            raise ValueError("status must be one of: open, in_progress, resolved, all")
    return serialize(items)


@mcp.tool()
def list_luggage_holds(active_only: bool = True) -> List[Dict[str, Any]]:
    """Luggage currently being held (or all, if active_only is False)."""
    return serialize(client.luggage_holds(active_only=active_only))


@mcp.tool()
def list_properties() -> List[Dict[str, Any]]:
    """All managed properties with manager, access code, wifi and parking notes."""
    return serialize(client.list_properties())


@mcp.tool()
def get_property(property_id: str) -> Dict[str, Any]:
    """Full detail for one property by id (e.g. 'P1')."""
    prop = client.get_property(property_id)
    if prop is None:
        raise ValueError(f"No property {property_id!r}")
    return serialize(prop)


@mcp.tool()
def get_reservation(reservation_id: str) -> Dict[str, Any]:
    """Full detail for one reservation (e.g. 'R1'), including its guest and property."""
    res = client.get_reservation(reservation_id)
    if res is None:
        raise ValueError(f"No reservation {reservation_id!r}")
    return serialize(
        {
            "reservation": res,
            "guest": client.get_guest(res.guest_id),
            "property": client.get_property(res.property_id),
        }
    )


@mcp.tool()
def add_reservation_note(reservation_id: str, note: str) -> Dict[str, Any]:
    """Append an operational note to a reservation (e.g. a turnover instruction)."""
    res = client.add_note(reservation_id, note)
    return serialize(res)


@mcp.tool()
def send_guest_message(reservation_id: str, message: str) -> Dict[str, Any]:
    """Queue a message to the guest on a reservation via their booking channel.

    NOTE: with the mock backend this does not actually send anything; it returns
    the message that *would* be sent so you can confirm before going live.
    """
    return serialize(client.send_guest_message(reservation_id, message))


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
