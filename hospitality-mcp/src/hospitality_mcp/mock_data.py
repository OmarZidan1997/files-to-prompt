"""A realistic in-memory dataset, generated relative to *today*.

Everything is anchored to ``date.today()`` so that asking for "tomorrow" always
returns meaningful turnovers no matter when the server runs. This is the data a
real PMS API would otherwise provide.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Dict, List

from .models import (
    Complaint,
    ComplaintStatus,
    Guest,
    LuggageHold,
    Property,
    Reservation,
    Severity,
)


def _d(days: int, *, today: date) -> date:
    return today + timedelta(days=days)


def build_dataset(today: date) -> Dict[str, object]:
    properties: List[Property] = [
        Property(
            id="P1",
            name="Sunset Villa",
            address="120 Ocean Dr, Santa Monica, CA",
            bedrooms=3,
            manager="Maria Lopez",
            access_code="4821",
            wifi="SunsetGuest / shells2024",
            parking_notes="Two spots in driveway; do not block neighbor's gate.",
        ),
        Property(
            id="P2",
            name="Harbor Loft",
            address="55 Pier Ave, Unit 4, Long Beach, CA",
            bedrooms=2,
            manager="Maria Lopez",
            access_code="9930",
            wifi="HarborLoft5G / anchor!88",
            parking_notes="Garage stall #4. Permit on dashboard for street.",
        ),
        Property(
            id="P3",
            name="Pinewood Cabin",
            address="7 Ridge Trail, Big Bear, CA",
            bedrooms=4,
            manager="Devon Park",
            access_code="1107",
            wifi="Pinewood / treeline",
            parking_notes="Snow chains required Nov-Mar. Park off the road.",
        ),
        Property(
            id="P4",
            name="Downtown Studio",
            address="900 Hope St, Apt 12B, Los Angeles, CA",
            bedrooms=1,
            manager="Devon Park",
            access_code="2255",
            wifi="DTLA12B / cityview",
            parking_notes="No on-site parking. Nearest lot: 5th & Hope ($25/day).",
        ),
    ]

    guests: List[Guest] = [
        Guest("G1", "Alvarez Family", "+1-310-555-0101", "alvarez@example.com", 4),
        Guest("G2", "Jordan Brooks", "+1-310-555-0102", "jbrooks@example.com", 2, vip=True),
        Guest("G3", "Wei Chen", "+1-562-555-0103", "wchen@example.com", 3),
        Guest("G4", "Sofia Diaz", "+1-562-555-0104", "sdiaz@example.com", 2),
        Guest("G5", "Tom Evans", "+1-909-555-0105", "tevans@example.com", 6),
        Guest("G6", "Priya Foster", "+1-213-555-0106", "pfoster@example.com", 1),
        Guest("G7", "Hannah Reed", "+1-909-555-0107", "hreed@example.com", 5),
        Guest("G8", "Marco Bianchi", "+1-213-555-0108", "mbianchi@example.com", 2, language="it"),
    ]

    reservations: List[Reservation] = [
        # --- Sunset Villa (P1): same-day turnaround tomorrow ---
        Reservation(
            "R1", "P1", "G1", _d(-3, today=today), _d(1, today=today),
            source="Airbnb", late_check_out=True, luggage_hold=True,
            notes="Requested late checkout (1pm). Leaving 3 bags for afternoon pickup.",
        ),
        Reservation(
            "R2", "P1", "G2", _d(1, today=today), _d(5, today=today),
            source="Direct", early_check_in=True,
            notes="VIP repeat guest. Wants champagne + early 12pm check-in if cleaning allows.",
        ),
        # --- Harbor Loft (P2): checkout tomorrow, new check-in tomorrow ---
        Reservation(
            "R3", "P2", "G3", _d(-2, today=today), _d(1, today=today),
            source="VRBO",
            notes="Open AC complaint - see complaint C1.",
        ),
        Reservation(
            "R4", "P2", "G4", _d(1, today=today), _d(4, today=today),
            source="Booking.com", early_check_in=True,
            notes="Flight lands 9am, asked to drop luggage early.",
        ),
        # --- Pinewood Cabin (P3): mid-stay, no turnover tomorrow ---
        Reservation(
            "R5", "P3", "G5", _d(0, today=today), _d(5, today=today),
            source="Airbnb",
            notes="Large party (6). Noise complaint last night - now resolved.",
        ),
        # --- Downtown Studio (P4): check-in only tomorrow (was vacant) ---
        Reservation(
            "R6", "P4", "G6", _d(1, today=today), _d(3, today=today),
            source="Airbnb",
            notes="Solo business traveler. Quiet, self check-in.",
        ),
        # --- Pinewood Cabin (P3): checkout the day after tomorrow ---
        Reservation(
            "R7", "P3", "G7", _d(-1, today=today), _d(2, today=today),
            source="Direct",
            notes="",
        ),
        # --- Harbor Loft (P2): future check-in, no impact tomorrow ---
        Reservation(
            "R8", "P2", "G8", _d(4, today=today), _d(7, today=today),
            source="Airbnb",
            notes="Italian-speaking guest; send arrival info in Italian.",
        ),
    ]

    complaints: List[Complaint] = [
        Complaint(
            "C1", "P2", "R3", category="maintenance", severity=Severity.HIGH,
            description="AC not cooling below 78F in the bedroom. Guest checking out tomorrow "
            "- MUST be fixed before next check-in (R4) same day.",
            status=ComplaintStatus.OPEN, created_on=_d(-1, today=today),
        ),
        Complaint(
            "C2", "P3", "R5", category="noise", severity=Severity.MEDIUM,
            description="Neighbor reported loud music after 10pm. Spoke with guest, apologized.",
            status=ComplaintStatus.RESOLVED, created_on=_d(-1, today=today),
        ),
        Complaint(
            "C3", "P1", "R1", category="amenity", severity=Severity.LOW,
            description="Coffee maker leaking. Replace before next guest (VIP R2).",
            status=ComplaintStatus.IN_PROGRESS, created_on=_d(0, today=today),
        ),
    ]

    luggage_holds: List[LuggageHold] = [
        LuggageHold(
            "L1", "P1", "R1", guest_name="Alvarez Family", bags=3,
            drop_time="tomorrow 1:00pm (after late checkout)",
            pickup_time="tomorrow ~5:00pm", status="stored",
        ),
        LuggageHold(
            "L2", "P2", "R4", guest_name="Sofia Diaz", bags=2,
            drop_time="tomorrow 9:30am (early, pre-check-in)",
            pickup_time="tomorrow 3:00pm at check-in", status="stored",
        ),
    ]

    # Per-property cleaning assignments used to populate turnovers.
    cleaners: Dict[str, str] = {
        "P1": "Bright & Tidy Co.",
        "P2": "Bright & Tidy Co.",
        "P3": "Mountain Maids",
        "P4": "City Sparkle",
    }

    return {
        "properties": properties,
        "guests": guests,
        "reservations": reservations,
        "complaints": complaints,
        "luggage_holds": luggage_holds,
        "cleaners": cleaners,
    }
