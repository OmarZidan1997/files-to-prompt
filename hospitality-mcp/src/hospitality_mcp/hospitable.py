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

DEFAULT_BASE_URL = "https://public.api.hospitable.com/v2/"


class HospitableClient(HospitalityClient):
    def __init__(
        self,
        token: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        window_days: int = 21,
        timeout: float = 30.0,
        transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        if not token:
            raise ValueError("A Hospitable Personal Access Token is required.")
        # base_url must keep a trailing slash so relative paths join correctly.
        base = base_url if base_url.endswith("/") else base_url + "/"
        self.window_days = window_days
        self._http = httpx.Client(
            base_url=base,
            timeout=timeout,
            transport=transport,
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
    def _get_collection(self, path: str, params: Optional[Dict[str, Any]] = None) -> List[dict]:
        """GET a paginated collection, following ``links.next`` to the end."""
        items: List[dict] = []
        url: Optional[str] = path
        while url:
            resp = self._http.get(url, params=params)
            resp.raise_for_status()
            payload = resp.json()
            data = payload.get("data", payload)
            if isinstance(data, list):
                items.extend(data)
            else:  # defensive: a single object where a list was expected
                items.append(data)
            url = (payload.get("links") or {}).get("next")
            params = None  # the next link already carries the query string
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

        return Reservation(
            id=str(r.get("id", "")),
            property_id=self._property_id_of(r),
            guest_id=guest_id,
            check_in=self._parse_date(r.get("check_in") or r.get("arrival_date")),
            check_out=self._parse_date(r.get("check_out") or r.get("departure_date")),
            source=str(r.get("platform") or r.get("channel") or "unknown"),
            status=str(status or "confirmed"),
            notes="" if party in (None, 0) else f"Party of {party}.",
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

    def complaints(self, *, status: Optional[ComplaintStatus] = None) -> List[Complaint]:
        return []  # no native complaints concept in Hospitable

    def luggage_holds(self, *, active_only: bool = True) -> List[LuggageHold]:
        return []  # no native luggage concept in Hospitable

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
