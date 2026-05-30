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
        {  # check-in only on DAY at prop-2
            "id": "res-in-2",
            "platform": "booking",
            "properties": [{"id": "prop-2"}],
            "check_in": "2026-06-01T15:00:00Z",
            "check_out": "2026-06-04T11:00:00Z",
            "status": {"current": "accepted"},
            "guests": {"total": 3},
            "guest": {"id": "g-3", "first_name": "Wei", "last_name": "Chen"},
        },
    ],
    "links": {"next": None},
}


def _handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith("/properties"):
        return httpx.Response(200, json=PROPERTIES)
    if path.endswith("/reservations"):
        return httpx.Response(200, json=RESERVATIONS)
    if path.endswith("/messages") and request.method == "POST":
        return httpx.Response(201, json={"data": {"id": "msg-1"}})
    return httpx.Response(404, json={"message": f"unhandled {path}"})


@pytest.fixture
def client():
    return HospitableClient(token="test-token", transport=httpx.MockTransport(_handler))


def test_properties_mapped(client):
    props = {p.id: p for p in client.list_properties()}
    assert props["prop-1"].name == "Sunset Villa"
    assert props["prop-1"].bedrooms == 3
    assert "Santa Monica" in props["prop-1"].address


def test_check_ins_and_outs(client):
    assert {r.property_id for r in client.check_ins_on(DAY)} == {"prop-1", "prop-2"}
    assert {r.property_id for r in client.check_outs_on(DAY)} == {"prop-1"}


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


def test_no_native_complaints_or_luggage(client):
    assert client.complaints() == []
    assert client.luggage_holds() == []
    assert client.cleaner_for("prop-1") is None
