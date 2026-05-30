"""Domain models for the hospitality MCP server.

These dataclasses describe the entities a property / vacation-rental operation
cares about: guests, properties, reservations, turnovers, complaints and
luggage holds. They are intentionally storage-agnostic so the same shapes work
whether the data comes from the bundled mock store or a real PMS API later.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Any, List, Optional


class CleaningStatus(str, Enum):
    NOT_SCHEDULED = "not_scheduled"
    SCHEDULED = "scheduled"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    BLOCKED = "blocked"


class ComplaintStatus(str, Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


@dataclass
class Guest:
    id: str
    name: str
    phone: str
    email: str
    party_size: int
    vip: bool = False
    language: str = "en"


@dataclass
class Property:
    id: str
    name: str
    address: str
    bedrooms: int
    manager: str
    access_code: str
    wifi: str
    parking_notes: str = ""


@dataclass
class Reservation:
    id: str
    property_id: str
    guest_id: str
    check_in: date
    check_out: date
    source: str  # Airbnb, VRBO, Direct, Booking.com ...
    status: str = "confirmed"
    early_check_in: bool = False
    late_check_out: bool = False
    luggage_hold: bool = False
    notes: str = ""


@dataclass
class Complaint:
    id: str
    property_id: str
    reservation_id: Optional[str]
    category: str  # noise, cleanliness, maintenance, amenity ...
    severity: Severity
    description: str
    status: ComplaintStatus
    created_on: date


@dataclass
class LuggageHold:
    id: str
    property_id: str
    reservation_id: str
    guest_name: str
    bags: int
    drop_time: str
    pickup_time: str
    status: str = "stored"  # stored, picked_up


@dataclass
class Turnover:
    """A property changeover on a given day.

    A turnover exists when a property has a check-out, a check-in, or both on
    the same date. Same-day turnarounds (check-out and check-in on one day) are
    the highest-pressure events and are flagged as such.
    """

    property_id: str
    property_name: str
    date: date
    check_out_reservation_id: Optional[str]
    check_in_reservation_id: Optional[str]
    cleaning_status: CleaningStatus
    cleaner: Optional[str]
    same_day_turnaround: bool
    priority: str  # low, normal, high
    notes: List[str] = field(default_factory=list)


def serialize(obj: Any) -> Any:
    """Recursively convert dataclasses / dates / enums into JSON-safe values."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: serialize(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: serialize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [serialize(v) for v in obj]
    return obj
