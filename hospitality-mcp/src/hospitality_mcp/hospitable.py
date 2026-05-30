"""Live client for the Hospitable Public API v2.

Implements the same ``HospitalityClient`` interface as the mock, so every MCP
tool works unchanged against real Hospitable data. Naming follows Hospitable's
own model: *properties*, *reservations*, *guests*, and reservation *messages*.

Docs: https://developer.hospitable.com/docs/public-api-docs/
  Base URL : https://public.api.hospitable.com/v2/
  Auth     : Authorization: Bearer <Personal Access Token>
  Endpoints used:
    GET  properties
    GET  reservations?properties[]=<uuid>&start_date=&end_date=&include=guest,properties
    GET  reservations/{id}?include=guest,properties
    GET  reservations/{id}/messages
    POST reservations/{id}/messages   (send a guest message)

Hospitable has no native concept of cleaning crews, complaints, or luggage
holds, so ``cleaner_for`` / ``complaints`` / ``luggage_holds`` return empty.
Turnovers are still derived from real check-in / check-out data by the shared
logic in the base class. (Those gaps could later be filled by scanning guest
messages or an external ops system.)
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

import httpx

from .client import HospitalityClient
from .models import (
    Complaint,
    ComplaintStatus,
    Guest,
    LuggageHold,
    Property,
    Reservation,
)

from .models import Severity

DEFAULT_BASE_URL = "https://public.api.hospitable.com/v2/"

# Keyword heuristics for turning free-text guest messages into structured
# complaints / luggage holds (Hospitable has no native objects for these).
_URGENT_TERMS = ("refund", "emergency", "unacceptable", "flooded", "no water", "locked out")
_HIGH_TERMS = (
    "broken", "not working", "doesn't work", "no hot water", "leak", "leaking",
    "no heat", "no power", "no electricity", "bed bugs", "roaches", "infestation",
)
# category -> trigger terms (first match wins)
_COMPLAINT_CATEGORIES = {
    "maintenance": (
        "broken", "not working", "doesn't work", "leak", "leaking", "no hot water",
        "no heat", "no power", "no electricity", "ac ", "air conditioning", "heater",
        "toilet", "plumbing", "fix",
    ),
    "cleanliness": ("dirty", "not clean", "stained", "smell", "trash", "bugs", "roaches", "bed bugs"),
    "connectivity": ("wifi", "wi-fi", "internet", "no signal"),
    "noise": ("noise", "noisy", "loud", "party next"),
    "access": ("locked out", "can't get in", "cannot get in", "code doesn't", "key", "lockbox"),
}
_COMPLAINT_TERMS = ("complaint", "unhappy", "disappointed", "issue", "problem", "not happy")
_LUGGAGE_TERMS = ("luggage", "suitcase", "bags", "store my bag", "drop my bag", "drop off bag", "leave my bag")


def _snippet(text: str, limit: int = 200) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def classify_complaint(text: str):
    """Return (category, Severity) if the message reads like a complaint, else None."""
    low = text.lower()
    category = None
    for cat, terms in _COMPLAINT_CATEGORIES.items():
        if any(t in low for t in terms):
            category = cat
            break
    if category is None and any(t in low for t in _COMPLAINT_TERMS):
        category = "general"
    if category is None:
        return None
    if any(t in low for t in _URGENT_TERMS):
        severity = Severity.URGENT
    elif any(t in low for t in _HIGH_TERMS):
        severity = Severity.HIGH
    elif category in ("general", "connectivity"):
        severity = Severity.LOW
    else:
        severity = Severity.MEDIUM
    return category, severity


def mentions_luggage(text: str) -> bool:
    return any(t in text.lower() for t in _LUGGAGE_TERMS)


class HospitableClient(HospitalityClient):
    def __init__(
        self,
        token: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        window_days: int = 21,
        timeout: float = 30.0,
        scan_messages: bool = False,
        message_recency_days: int = 14,
        max_scan_reservations: int = 60,
        transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        if not token:
            raise ValueError("A Hospitable Personal Access Token is required.")
        # base_url must keep a trailing slash so relative paths join correctly.
        base = base_url if base_url.endswith("/") else base_url + "/"
        self.window_days = window_days
        self.scan_messages = scan_messages
        self.message_recency_days = message_recency_days
        self.max_scan_reservations = max_scan_reservations
        self._messages: Dict[str, List[dict]] = {}
        self._http = httpx.Client(
            base_url=base,
            timeout=timeout,
            transport=transport,
            follow_redirects=True,  # Hospitable's pagination links use http:// -> 307 https
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        self._properties: Optional[List[Property]] = None
        self._guests: Dict[str, Guest] = {}
        self._reservation_windows: Dict[tuple, List[Reservation]] = {}

    # --- HTTP plumbing -----------------------------------------------------
    def _get_collection(
        self, path: str, params: Optional[Dict[str, Any]] = None, *, max_pages: int = 50
    ) -> List[dict]:
        """GET a paginated collection by incrementing ``page``.

        We page manually (rather than following ``links.next``) because
        Hospitable's next-link drops required query params like ``properties[]``,
        which 400s. Re-sending the original params each page is reliable.
        """
        items: List[dict] = []
        query: Dict[str, Any] = dict(params or {})
        for page in range(1, max_pages + 1):
            query["page"] = page
            resp = self._http.get(path, params=query)
            resp.raise_for_status()
            payload = resp.json()
            data = payload.get("data", payload)
            if not isinstance(data, list):  # single object where a list was expected
                items.append(data)
                break
            items.extend(data)
            meta = payload.get("meta") or {}
            last_page = meta.get("last_page")
            current = meta.get("current_page", page)
            if last_page is not None:
                if current >= last_page:
                    break
            elif not (payload.get("links") or {}).get("next"):
                break
        return items

    def _get_one(self, path: str, params: Optional[Dict[str, Any]] = None) -> Optional[dict]:
        resp = self._http.get(path, params=params)
        resp.raise_for_status()
        payload = resp.json()
        data = payload.get("data", payload)
        return data if isinstance(data, dict) else None

    # --- mapping Hospitable JSON -> our models -----------------------------
    @staticmethod
    def _parse_date(value: Any) -> Optional[date]:
        if not value:
            return None
        if isinstance(value, dict):  # some fields come as {"date": "...", "time": "..."}
            value = value.get("datetime") or value.get("date") or ""
        text = str(value)
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
        except ValueError:
            return datetime.strptime(text[:10], "%Y-%m-%d").date()

    @staticmethod
    def _property_id_of(reservation: dict) -> str:
        props = reservation.get("properties")
        if isinstance(props, list) and props:
            first = props[0]
            return str(first.get("id") if isinstance(first, dict) else first)
        listings = reservation.get("listings")
        if isinstance(listings, list) and listings:
            first = listings[0]
            if isinstance(first, dict):
                return str(first.get("property_id") or first.get("id") or "")
            return str(first)
        return str(reservation.get("property_id") or "")

    def _map_property(self, p: dict) -> Property:
        address = p.get("address")
        if isinstance(address, dict):
            address_str = ", ".join(
                str(part)
                for part in (
                    address.get("street") or address.get("line1"),
                    address.get("city"),
                    address.get("state"),
                    address.get("zip") or address.get("postcode"),
                    address.get("country"),
                )
                if part
            )
        else:
            address_str = str(address or "")
        capacity = p.get("capacity") if isinstance(p.get("capacity"), dict) else {}
        bedrooms = capacity.get("bedrooms", p.get("bedrooms", 0)) or 0
        return Property(
            id=str(p.get("id", "")),
            name=p.get("name") or p.get("public_name") or "Unnamed property",
            address=address_str,
            bedrooms=int(bedrooms),
            manager="",  # not exposed by the Hospitable API
            access_code="",
            wifi="",
            parking_notes="",
        )

    def _map_guest(self, g: dict) -> Optional[Guest]:
        gid = g.get("id")
        if not gid:
            return None
        first = g.get("first_name") or ""
        last = g.get("last_name") or ""
        name = (f"{first} {last}".strip()) or g.get("name") or "Guest"
        phones = g.get("phone_numbers")
        phone = g.get("phone") or (phones[0] if isinstance(phones, list) and phones else "")
        guest = Guest(
            id=str(gid),
            name=name,
            phone=str(phone or ""),
            email=str(g.get("email") or ""),
            party_size=0,
            vip=False,
            language=str(g.get("locale") or g.get("language") or "en")[:2],
        )
        self._guests[guest.id] = guest
        return guest

    def _map_reservation(self, r: dict) -> Reservation:
        guest_blob = r.get("guest")
        guest_id = ""
        if isinstance(guest_blob, dict) and guest_blob:
            mapped = self._map_guest(guest_blob)
            guest_id = mapped.id if mapped else ""
        elif isinstance(guest_blob, str):
            guest_id = guest_blob

        status = r.get("status")
        if isinstance(status, dict):
            status = status.get("current") or status.get("category") or "confirmed"

        guests = r.get("guests")
        if isinstance(guests, dict):
            party = guests.get("total")
        else:
            party = guests

        # Hospitable's own reservation note plus a party-size hint.
        note_parts = []
        if r.get("notes"):
            note_parts.append(str(r["notes"]))
        if party not in (None, 0):
            note_parts.append(f"Party of {party}.")

        return Reservation(
            id=str(r.get("id", "")),
            property_id=self._property_id_of(r),
            guest_id=guest_id,
            check_in=self._parse_date(r.get("check_in") or r.get("arrival_date")),
            check_out=self._parse_date(r.get("check_out") or r.get("departure_date")),
            source=str(r.get("platform") or r.get("channel") or "unknown"),
            status=str(status or "confirmed"),
            notes=" ".join(note_parts),
            issue_alert=(str(r["issue_alert"]) if r.get("issue_alert") else None),
        )

    # --- HospitalityClient interface ---------------------------------------
    def list_properties(self) -> List[Property]:
        if self._properties is None:
            self._properties = [self._map_property(p) for p in self._get_collection("properties")]
        return list(self._properties)

    def get_property(self, property_id: str) -> Optional[Property]:
        return next((p for p in self.list_properties() if p.id == property_id), None)

    def get_reservation(self, reservation_id: str) -> Optional[Reservation]:
        data = self._get_one(
            f"reservations/{reservation_id}", params={"include": "guest,properties"}
        )
        return self._map_reservation(data) if data else None

    def get_guest(self, guest_id: str) -> Optional[Guest]:
        # Guests are cached as reservations are loaded (Hospitable returns them
        # via include=guest rather than a standalone endpoint).
        return self._guests.get(guest_id)

    def cleaner_for(self, property_id: str) -> Optional[str]:
        return None  # not modeled by Hospitable's public API

    def _reservations_window(self, center: date) -> List[Reservation]:
        start = center - timedelta(days=self.window_days)
        end = center + timedelta(days=self.window_days)
        key = (start, end)
        if key not in self._reservation_windows:
            params: Dict[str, Any] = {
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "include": "guest,properties",
            }
            uuids = [p.id for p in self.list_properties() if p.id]
            if uuids:
                params["properties[]"] = uuids
            raw = self._get_collection("reservations", params=params)
            self._reservation_windows[key] = [self._map_reservation(r) for r in raw]
        return self._reservation_windows[key]

    def check_ins_on(self, day: date) -> List[Reservation]:
        return [r for r in self._reservations_window(day) if r.check_in == day]

    def check_outs_on(self, day: date) -> List[Reservation]:
        return [r for r in self._reservations_window(day) if r.check_out == day]

    # --- guest-message scanning (derives complaints / luggage) -------------
    def _scan_targets(self) -> List[Reservation]:
        """Reservations to scan for messages: those in already-loaded windows,
        or today's window if nothing has been loaded yet. Capped for safety."""
        seen: Dict[str, Reservation] = {}
        for window in self._reservation_windows.values():
            for r in window:
                if r.id:
                    seen.setdefault(r.id, r)
        if not seen:
            for r in self._reservations_window(date.today()):
                if r.id:
                    seen.setdefault(r.id, r)
        return list(seen.values())[: self.max_scan_reservations]

    def _messages_for(self, reservation_id: str) -> List[dict]:
        if reservation_id not in self._messages:
            try:
                self._messages[reservation_id] = self._get_collection(
                    f"reservations/{reservation_id}/messages"
                )
            except httpx.HTTPError:
                self._messages[reservation_id] = []
        return self._messages[reservation_id]

    @staticmethod
    def _message_body(msg: dict) -> str:
        return str(msg.get("body") or msg.get("message") or msg.get("text") or "")

    @staticmethod
    def _is_guest_message(msg: dict) -> bool:
        role = str(
            msg.get("sender_role") or msg.get("sender_type") or msg.get("sender") or ""
        ).lower()
        if not role:
            return True  # unknown sender: don't drop a potential complaint
        return "host" not in role and "user" not in role and "system" not in role

    def _guest_messages(self, reservation: Reservation):
        """Yield (body, created_on) for recent guest-sent messages."""
        cutoff = date.today() - timedelta(days=self.message_recency_days)
        for msg in self._messages_for(reservation.id):
            if not self._is_guest_message(msg):
                continue
            body = self._message_body(msg)
            if not body:
                continue
            created = self._parse_date(msg.get("created_at") or msg.get("sent_at")) or date.today()
            if created < cutoff:
                continue
            yield body, created

    def complaints(self, *, status: Optional[ComplaintStatus] = None) -> List[Complaint]:
        # Primary source: Hospitable's native per-reservation ``issue_alert``
        # (already present on loaded reservations — no extra API calls).
        found: List[Complaint] = []
        for res in self._scan_targets():
            if res.issue_alert:
                found.append(
                    Complaint(
                        id=f"alert-{res.id}",
                        property_id=res.property_id,
                        reservation_id=res.id,
                        category="issue",
                        severity=Severity.HIGH,
                        description=f"Hospitable issue alert: {res.issue_alert}",
                        status=ComplaintStatus.OPEN,
                        created_on=res.check_in or date.today(),
                    )
                )

        # Optional supplement: scan recent guest messages for complaint keywords
        # (costs one API call per reservation, so it is off by default).
        for res in self._scan_targets() if self.scan_messages else []:
            for body, created in self._guest_messages(res):
                result = classify_complaint(body)
                if result is None:
                    continue
                category, severity = result
                found.append(
                    Complaint(
                        id=f"msg-{res.id}-{len(found)}",
                        property_id=res.property_id,
                        reservation_id=res.id,
                        category=category,
                        severity=severity,
                        description=f"(auto-detected from guest message) {_snippet(body)}",
                        status=ComplaintStatus.OPEN,
                        created_on=created,
                    )
                )
        if status is not None:
            found = [c for c in found if c.status == status]
        sev_order = {"urgent": 0, "high": 1, "medium": 2, "low": 3}
        found.sort(key=lambda c: sev_order.get(c.severity.value, 4))
        return found

    def luggage_holds(self, *, active_only: bool = True) -> List[LuggageHold]:
        # Derived from guest messages — Hospitable has no native luggage object.
        if not self.scan_messages:
            return []
        holds: List[LuggageHold] = []
        for res in self._scan_targets():
            guest = self.get_guest(res.guest_id)
            for body, _created in self._guest_messages(res):
                if not mentions_luggage(body):
                    continue
                holds.append(
                    LuggageHold(
                        id=f"msg-{res.id}-{len(holds)}",
                        property_id=res.property_id,
                        reservation_id=res.id,
                        guest_name=guest.name if guest else res.guest_id,
                        bags=0,  # not parseable from free text
                        drop_time="",
                        pickup_time="",
                        status="stored",
                        note=f"(auto-detected from guest message) {_snippet(body)}",
                    )
                )
        return holds

    def add_note(self, reservation_id: str, note: str) -> Reservation:
        raise NotImplementedError(
            "Hospitable's public API does not support writing reservation notes. "
            "Use send_guest_message to message the guest instead."
        )

    def send_guest_message(self, reservation_id: str, message: str) -> Dict[str, str]:
        resp = self._http.post(
            f"reservations/{reservation_id}/messages", json={"body": message}
        )
        resp.raise_for_status()
        return {
            "reservation_id": reservation_id,
            "status": "sent",
            "message": message,
        }
