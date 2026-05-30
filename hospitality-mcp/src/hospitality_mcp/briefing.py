"""Composite operational views built on top of a HospitalityClient.

These are the *custom* capabilities the project adds — a one-shot daily turnover
briefing and a turnover list — on top of whatever raw endpoints/tools a PMS
exposes. Kept backend-agnostic so they work over mock data, the Hospitable
Public API, or anything else implementing ``HospitalityClient``.
"""

from __future__ import annotations

from typing import Any, Dict, List

from .client import HospitalityClient
from .dates import DateParseError, parse_day
from .models import ComplaintStatus, serialize


def _resolve_day(day: str):
    try:
        return parse_day(day)
    except DateParseError as exc:
        raise ValueError(str(exc)) from exc


def daily_briefing(client: HospitalityClient, day: str = "tomorrow") -> Dict[str, Any]:
    """Full operational briefing for a day: turnovers, check-ins/outs, open
    complaints/issues, luggage holds and prioritized alerts."""
    d = _resolve_day(day)
    turnovers = client.turnovers_on(d)
    check_ins = client.check_ins_on(d)
    check_outs = client.check_outs_on(d)
    open_complaints = [
        c for c in client.complaints() if c.status != ComplaintStatus.RESOLVED
    ]
    luggage = client.luggage_holds(active_only=True)

    alerts: List[str] = []
    for t in turnovers:
        if t.same_day_turnaround:
            alerts.append(
                f"SAME-DAY TURNAROUND at {t.property_name}: clean between checkout and check-in."
            )
        elif t.priority == "high":
            alerts.append(f"High-priority turnover at {t.property_name}.")
    for c in open_complaints:
        if c.severity.value in ("high", "urgent"):
            prop = client.get_property(c.property_id)
            name = prop.name if prop else c.property_id
            alerts.append(f"{c.severity.value.upper()} issue at {name}: {c.description}")

    return serialize(
        {
            "date": d,
            "summary": {
                "turnovers": len(turnovers),
                "check_ins": len(check_ins),
                "check_outs": len(check_outs),
                "open_complaints": len(open_complaints),
                "luggage_holds": len(luggage),
                "same_day_turnarounds": sum(1 for t in turnovers if t.same_day_turnaround),
            },
            "alerts": alerts,
            "turnovers": turnovers,
            "check_ins": check_ins,
            "check_outs": check_outs,
            "open_complaints": open_complaints,
            "luggage_holds": luggage,
        }
    )


def list_turnovers(client: HospitalityClient, day: str = "tomorrow") -> List[Dict[str, Any]]:
    """Property turnovers for a day, priority-sorted, each with cleaner + notes."""
    return serialize(client.turnovers_on(_resolve_day(day)))
