"""Tests for the live Hospitable client using a mocked HTTP transport.

No network or real token needed: httpx.MockTransport serves canned Hospitable
v2 responses so we can verify JSON->model mapping and that the shared turnover
derivation works over real-shaped data.
"""

from datetime import date

import httpx
import pytest

from hospitality_mcp.hospitable import HospitableClient

DAY = date(2026, 6, 1)

PROPERTIES = {
    "data": [
        {
            "id": "prop-1",
            "name": "Sunset Villa",
            "address": {"street": "120 Ocean Dr", "city": "Santa Monica", "state": "CA"},
            "capacity": {"bedrooms": 3},
        },
        {
            "id": "prop-2",
            "name": "Harbor Loft",
            "address": {"street": "55 Pier Ave", "city": "Long Beach", "state": "CA"},
            "capacity": {"bedrooms": 2},
        },
    ]
}

RESERVATIONS = {
    "data": [
        {  # checkout on DAY at prop-1
            "id": "res-out-1",
            "platform": "airbnb",
            "properties": [{"id": "prop-1"}],
            "check_in": "2026-05-28T16:00:00Z",
            "check_out": "2026-06-01T11:00:00Z",
            "status": {"current": "checkout_today"},
            "guests": {"total": 4},
            "guest": {"id": "g-1", "first_name": "Ana", "last_name": "Alvarez"},
        },
        {  # check-in on DAY at prop-1 -> same-day turnaround
            "id": "res-in-1",
            "platform": "direct",
            "properties": [{"id": "prop-1"}],
            "check_in": "2026-06-01T16:00:00Z",
            "check_out": "2026-06-05T11:00:00Z",
            "status": {"current": "accepted"},
            "guests": {"total": 2},
            "guest": {"id": "g-2", "first_name": "Jordan", "last_name": "Brooks"},
        },
        {  # check-in only on DAY at prop-2, with a native Hospitable issue alert
            "id": "res-in-2",
            "platform": "booking",
            "properties": [{"id": "prop-2"}],
            "check_in": "2026-06-01T15:00:00Z",
            "check_out": "2026-06-04T11:00:00Z",
            "status": {"current": "accepted"},
            "guests": {"total": 3},
            "notes": "Repeat guest; prefers ground floor.",
            "issue_alert": "Guest reported the heater is not working",
            "guest": {"id": "g-3", "first_name": "Wei", "last_name": "Chen"},
        },
        {  # CANCELLED checkout on DAY at prop-2 -> must NOT count as a check-out,
           # and must NOT make prop-2 a phantom same-day turnaround.
            "id": "res-out-2-cancelled",
            "platform": "airbnb",
            "properties": [{"id": "prop-2"}],
            "check_in": "2026-05-30T16:00:00Z",
            "check_out": "2026-06-01T11:00:00Z",
            "status": {"current": "cancelled"},
            "guests": {"total": 2},
            "guest": {"id": "g-4", "first_name": "Sam", "last_name": "Doe"},
        },
    ],
    "links": {"next": None},
}


MESSAGES = {
    "res-out-1": {
        "data": [
            {  # genuine guest dialog -> a complaint
                "id": "m1",
                "sender_type": "guest",
                "source": "platform",
                "body": "Hi, the AC is not working and the bedroom is very hot.",
                "created_at": "2026-05-29T10:00:00Z",
            },
            {  # genuine host reply -> not a guest message, ignored
                "id": "m2",
                "sender_type": "host",
                "source": "platform",
                "body": "So sorry! Sending someone to fix the air conditioning today.",
                "created_at": "2026-05-29T10:05:00Z",
            },
            {  # genuine guest dialog -> a luggage hold
                "id": "m3",
                "sender_type": "guest",
                "source": "platform",
                "body": "Also, can I store my luggage after checkout until 5pm?",
                "created_at": "2026-05-29T10:10:00Z",
            },
            {  # AUTOMATED guest-channel message with a complaint keyword -> MUST be ignored
                "id": "m4",
                "sender_type": "guest",
                "source": "automated",
                "body": "Reminder: the heater is not working schedule has changed.",
                "created_at": "2026-05-29T10:20:00Z",
            },
            {  # AI auto-reply -> MUST be ignored
                "id": "m5",
                "sender_type": "host",
                "source": "AI",
                "body": "No worries, the wifi is broken issue will be resolved.",
                "created_at": "2026-05-29T10:25:00Z",
            },
        ]
    }
}


def _handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith("/properties"):
        return httpx.Response(200, json=PROPERTIES)
    if path.endswith("/messages") and request.method == "POST":
        return httpx.Response(201, json={"data": {"id": "msg-new"}})
    if path.endswith("/messages"):
        res_id = path.split("/reservations/")[1].split("/")[0]
        return httpx.Response(200, json=MESSAGES.get(res_id, {"data": []}))
    if path.endswith("/reservations"):
        return httpx.Response(200, json=RESERVATIONS)
    return httpx.Response(404, json={"message": f"unhandled {path}"})


@pytest.fixture
def client():
    # Message scanning is on by default; large recency window so fixed message
    # dates aren't filtered by the wall clock.
    return HospitableClient(
        token="test-token",
        message_recency_days=100_000,
        transport=httpx.MockTransport(_handler),
    )


def test_properties_mapped(client):
    props = {p.id: p for p in client.list_properties()}
    assert props["prop-1"].name == "Sunset Villa"
    assert props["prop-1"].bedrooms == 3
    assert "Santa Monica" in props["prop-1"].address


def test_check_ins_and_outs(client):
    assert {r.property_id for r in client.check_ins_on(DAY)} == {"prop-1", "prop-2"}
    assert {r.property_id for r in client.check_outs_on(DAY)} == {"prop-1"}


def test_cancelled_reservations_are_excluded(client):
    # A cancelled checkout must not appear as a check-out...
    out_ids = {r.id for r in client.check_outs_on(DAY)}
    assert "res-out-2-cancelled" not in out_ids
    assert {r.property_id for r in client.check_outs_on(DAY)} == {"prop-1"}
    # ...and must not turn prop-2 into a phantom same-day turnaround.
    turnovers = {t.property_id: t for t in client.turnovers_on(DAY)}
    assert turnovers["prop-2"].same_day_turnaround is False


def test_turnover_derivation_over_live_shape(client):
    turnovers = {t.property_id: t for t in client.turnovers_on(DAY)}
    assert turnovers["prop-1"].same_day_turnaround is True
    assert turnovers["prop-2"].same_day_turnaround is False
    # same-day turnaround is prioritized high and sorts first
    assert client.turnovers_on(DAY)[0].property_id == "prop-1"


def test_guest_cached_via_include(client):
    client.check_ins_on(DAY)  # loads reservations + included guests
    guest = client.get_guest("g-2")
    assert guest is not None and guest.name == "Jordan Brooks"


def test_reservation_dates_and_source(client):
    res_by_prop = {r.property_id: r for r in client.check_ins_on(DAY)}
    r = res_by_prop["prop-2"]
    assert r.check_in == DAY
    assert r.check_out == date(2026, 6, 4)
    assert r.source == "booking"


def test_send_guest_message_posts(client):
    result = client.send_guest_message("res-in-1", "Welcome! Check-in is at 4pm.")
    assert result["status"] == "sent"
    assert result["reservation_id"] == "res-in-1"


def test_issue_alert_is_not_a_complaint(client):
    # The native issue_alert is intentionally NOT used as a complaint source.
    assert all(c.category != "issue" for c in client.complaints())
    assert all("issue alert" not in c.description.lower() for c in client.complaints())


def test_complaints_only_from_genuine_guest_dialog(client):
    complaints = client.complaints()
    # Exactly one: the genuine guest AC message. The host reply, the automated
    # "heater not working" reminder, and the AI "wifi broken" message are ignored.
    assert len(complaints) == 1
    c = complaints[0]
    assert c.property_id == "prop-1"
    assert c.category == "maintenance"
    assert c.severity.value == "high"  # "not working" -> high
    assert "from guest message" in c.description


def test_luggage_derived_from_guest_messages(client):
    holds = client.luggage_holds()
    assert len(holds) == 1
    assert holds[0].property_id == "prop-1"
    assert "luggage" in holds[0].note.lower()


def test_derived_complaint_feeds_turnover_notes(client):
    # The AC complaint at prop-1 should surface in tomorrow's turnover notes.
    t = next(t for t in client.turnovers_on(DAY) if t.property_id == "prop-1")
    assert any("from guest message" in n for n in t.notes)
    assert t.priority == "high"


def test_scanning_can_be_disabled():
    no_scan = HospitableClient(
        token="t", scan_messages=False, transport=httpx.MockTransport(_handler)
    )
    assert no_scan.complaints() == []
    assert no_scan.luggage_holds() == []
    assert no_scan.cleaner_for("prop-1") is None


def test_automated_messages_are_ignored(client):
    # Neither the automated reminder nor the AI reply should produce complaints.
    descriptions = " ".join(c.description.lower() for c in client.complaints())
    assert "heater" not in descriptions  # from the automated message
    assert "wifi" not in descriptions  # from the AI message
