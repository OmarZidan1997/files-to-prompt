"""Data-access layer for the hospitality MCP server.

``HospitalityClient`` is the interface the MCP tools depend on. ``MockClient``
implements it against the bundled in-memory dataset. To go live against a real
PMS (Turno, Guesty, Hostaway, a custom endpoint, ...), implement the same
methods in a new subclass that makes HTTP calls and return the same shapes — no
tool code needs to change.
"""

from __future__ import annotations

import abc
from datetime import date
from typing import Dict, List, Optional

from .mock_data import build_dataset
from .models import (
    CleaningStatus,
    Complaint,
    ComplaintStatus,
    Guest,
    LuggageHold,
    Property,
    Reservation,
    Turnover,
)


class HospitalityClient(abc.ABC):
    @abc.abstractmethod
    def list_properties(self) -> List[Property]: ...

    @abc.abstractmethod
    def get_property(self, property_id: str) -> Optional[Property]: ...

    @abc.abstractmethod
    def get_reservation(self, reservation_id: str) -> Optional[Reservation]: ...

    @abc.abstractmethod
    def get_guest(self, guest_id: str) -> Optional[Guest]: ...

    @abc.abstractmethod
    def cleaner_for(self, property_id: str) -> Optional[str]: ...

    @abc.abstractmethod
    def check_ins_on(self, day: date) -> List[Reservation]: ...

    @abc.abstractmethod
    def check_outs_on(self, day: date) -> List[Reservation]: ...

    @abc.abstractmethod
    def complaints(self, *, status: Optional[ComplaintStatus] = None) -> List[Complaint]: ...

    @abc.abstractmethod
    def luggage_holds(self, *, active_only: bool = True) -> List[LuggageHold]: ...

    @abc.abstractmethod
    def add_note(self, reservation_id: str, note: str) -> Reservation: ...

    @abc.abstractmethod
    def send_guest_message(self, reservation_id: str, message: str) -> Dict[str, str]: ...

    # --- shared derivations (built on the primitives above) ----------------
    def _property_name(self, property_id: str) -> str:
        prop = self.get_property(property_id)
        return prop.name if prop else property_id

    def turnovers_on(self, day: date) -> List[Turnover]:
        """Derive turnovers from check-ins and check-outs on ``day``.

        A property has a turnover when it has a check-out, a check-in, or both
        (a same-day turnaround) on the date. This logic is backend-agnostic: it
        relies only on the abstract primitives, so it works identically over
        mock data or a live PMS that exposes reservations.
        """
        check_outs = {r.property_id: r for r in self.check_outs_on(day)}
        check_ins = {r.property_id: r for r in self.check_ins_on(day)}
        property_ids = sorted(set(check_outs) | set(check_ins))
        open_complaints = [
            c for c in self.complaints() if c.status != ComplaintStatus.RESOLVED
        ]

        turnovers: List[Turnover] = []
        for pid in property_ids:
            out_res = check_outs.get(pid)
            in_res = check_ins.get(pid)
            same_day = out_res is not None and in_res is not None

            notes: List[str] = []
            if out_res:
                if out_res.late_check_out:
                    notes.append("Departing guest has a LATE checkout - cleaning starts later.")
                if out_res.luggage_hold:
                    notes.append("Departing guest is leaving luggage for later pickup.")
                if out_res.notes:
                    notes.append(f"Checkout note: {out_res.notes}")
            if in_res:
                if in_res.early_check_in:
                    notes.append("Arriving guest requested EARLY check-in - tight cleaning window.")
                guest = self.get_guest(in_res.guest_id)
                if guest and guest.vip:
                    notes.append("Arriving guest is a VIP - extra prep / welcome touch.")
                if in_res.notes:
                    notes.append(f"Check-in note: {in_res.notes}")

            open_here = [c for c in open_complaints if c.property_id == pid]
            for c in open_here:
                notes.append(f"OPEN issue ({c.severity.value}): {c.description}")

            if same_day:
                priority = "high"
            else:
                priority = "normal"
            if any(c.severity.value in ("high", "urgent") for c in open_here):
                priority = "high"

            turnovers.append(
                Turnover(
                    property_id=pid,
                    property_name=self._property_name(pid),
                    date=day,
                    check_out_reservation_id=out_res.id if out_res else None,
                    check_in_reservation_id=in_res.id if in_res else None,
                    cleaning_status=CleaningStatus.SCHEDULED,
                    cleaner=self.cleaner_for(pid),
                    same_day_turnaround=same_day,
                    priority=priority,
                    notes=notes,
                )
            )

        order = {"high": 0, "normal": 1, "low": 2}
        turnovers.sort(key=lambda t: order.get(t.priority, 1))
        return turnovers


class MockClient(HospitalityClient):
    """In-memory implementation backed by ``mock_data.build_dataset``."""

    def __init__(self, today: Optional[date] = None) -> None:
        self.today = today or date.today()
        data = build_dataset(self.today)
        self._properties: List[Property] = data["properties"]  # type: ignore[assignment]
        self._guests: List[Guest] = data["guests"]  # type: ignore[assignment]
        self._reservations: List[Reservation] = data["reservations"]  # type: ignore[assignment]
        self._complaints: List[Complaint] = data["complaints"]  # type: ignore[assignment]
        self._luggage: List[LuggageHold] = data["luggage_holds"]  # type: ignore[assignment]
        self._cleaners: Dict[str, str] = data["cleaners"]  # type: ignore[assignment]
        self._sent_messages: List[Dict[str, str]] = []

    # --- lookups -----------------------------------------------------------
    def list_properties(self) -> List[Property]:
        return list(self._properties)

    def get_property(self, property_id: str) -> Optional[Property]:
        return next((p for p in self._properties if p.id == property_id), None)

    def get_reservation(self, reservation_id: str) -> Optional[Reservation]:
        return next((r for r in self._reservations if r.id == reservation_id), None)

    def get_guest(self, guest_id: str) -> Optional[Guest]:
        return next((g for g in self._guests if g.id == guest_id), None)

    def cleaner_for(self, property_id: str) -> Optional[str]:
        return self._cleaners.get(property_id)

    # --- daily operations --------------------------------------------------
    def check_ins_on(self, day: date) -> List[Reservation]:
        return [r for r in self._reservations if r.check_in == day]

    def check_outs_on(self, day: date) -> List[Reservation]:
        return [r for r in self._reservations if r.check_out == day]

    def complaints(self, *, status: Optional[ComplaintStatus] = None) -> List[Complaint]:
        items = list(self._complaints)
        if status is not None:
            items = [c for c in items if c.status == status]
        sev_order = {"urgent": 0, "high": 1, "medium": 2, "low": 3}
        items.sort(key=lambda c: sev_order.get(c.severity.value, 4))
        return items

    def luggage_holds(self, *, active_only: bool = True) -> List[LuggageHold]:
        items = list(self._luggage)
        if active_only:
            items = [l for l in items if l.status != "picked_up"]
        return items

    # --- mutations (mock side effects) -------------------------------------
    def add_note(self, reservation_id: str, note: str) -> Reservation:
        res = self.get_reservation(reservation_id)
        if res is None:
            raise KeyError(f"No reservation {reservation_id!r}")
        res.notes = f"{res.notes}\n{note}".strip() if res.notes else note
        return res

    def send_guest_message(self, reservation_id: str, message: str) -> Dict[str, str]:
        res = self.get_reservation(reservation_id)
        if res is None:
            raise KeyError(f"No reservation {reservation_id!r}")
        guest = self.get_guest(res.guest_id)
        record = {
            "reservation_id": reservation_id,
            "to": guest.name if guest else res.guest_id,
            "channel": res.source,
            "message": message,
            "status": "queued (mock - not actually sent)",
        }
        self._sent_messages.append(record)
        return record
