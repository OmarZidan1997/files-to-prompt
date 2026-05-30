"""Tests for the data layer. These run without the `mcp` package installed."""

from datetime import date, timedelta

import pytest

from hospitality_mcp.client import MockClient
from hospitality_mcp.dates import DateParseError, parse_day
from hospitality_mcp.models import ComplaintStatus, serialize

TODAY = date(2026, 5, 30)
TOMORROW = TODAY + timedelta(days=1)


@pytest.fixture
def client():
    return MockClient(today=TODAY)


def test_parse_day_keywords():
    assert parse_day("today", today=TODAY) == TODAY
    assert parse_day("tomorrow", today=TODAY) == TOMORROW
    assert parse_day("yesterday", today=TODAY) == TODAY - timedelta(days=1)
    assert parse_day(None, today=TODAY) == TODAY
    assert parse_day("", today=TODAY) == TODAY


def test_parse_day_iso_and_offset():
    assert parse_day("2026-05-31", today=TODAY) == date(2026, 5, 31)
    assert parse_day("+2", today=TODAY) == TODAY + timedelta(days=2)
    assert parse_day("-1", today=TODAY) == TODAY - timedelta(days=1)


def test_parse_day_weekday_next_occurrence():
    # 2026-05-30 is a Saturday; "monday" should be the following Monday.
    assert parse_day("monday", today=TODAY) == date(2026, 6, 1)


def test_parse_day_invalid():
    with pytest.raises(DateParseError):
        parse_day("someday", today=TODAY)


def test_tomorrow_has_turnovers(client):
    turnovers = client.turnovers_on(TOMORROW)
    by_prop = {t.property_id: t for t in turnovers}
    # P1 and P2 are same-day turnarounds, P4 is a check-in only.
    assert {"P1", "P2", "P4"} <= set(by_prop)
    assert by_prop["P1"].same_day_turnaround is True
    assert by_prop["P2"].same_day_turnaround is True
    assert by_prop["P4"].same_day_turnaround is False


def test_turnovers_sorted_high_priority_first(client):
    turnovers = client.turnovers_on(TOMORROW)
    priorities = [t.priority for t in turnovers]
    assert priorities == sorted(priorities, key={"high": 0, "normal": 1, "low": 2}.get)


def test_turnover_notes_surface_important_context(client):
    p1 = next(t for t in client.turnovers_on(TOMORROW) if t.property_id == "P1")
    joined = " ".join(p1.notes).lower()
    assert "late" in joined  # late checkout
    assert "vip" in joined  # arriving VIP
    assert "luggage" in joined


def test_open_complaint_raises_property_priority(client):
    # Harbor Loft (P2) has an open HIGH AC complaint -> high priority turnover.
    p2 = next(t for t in client.turnovers_on(TOMORROW) if t.property_id == "P2")
    assert p2.priority == "high"


def test_check_ins_and_outs(client):
    ins = {r.property_id for r in client.check_ins_on(TOMORROW)}
    outs = {r.property_id for r in client.check_outs_on(TOMORROW)}
    assert {"P1", "P2", "P4"} <= ins
    assert {"P1", "P2"} <= outs


def test_complaints_filter_and_sort(client):
    open_only = client.complaints(status=ComplaintStatus.OPEN)
    assert all(c.status == ComplaintStatus.OPEN for c in open_only)
    all_sorted = client.complaints()
    sev = {"urgent": 0, "high": 1, "medium": 2, "low": 3}
    order = [sev[c.severity.value] for c in all_sorted]
    assert order == sorted(order)


def test_luggage_holds_active_only(client):
    holds = client.luggage_holds(active_only=True)
    assert all(h.status != "picked_up" for h in holds)
    assert len(holds) >= 2


def test_add_note_appends(client):
    res = client.add_note("R6", "Gate code changed to 7788.")
    assert "7788" in res.notes


def test_send_guest_message_is_mock(client):
    record = client.send_guest_message("R2", "Your early check-in is confirmed for 12pm.")
    assert record["to"] == "Jordan Brooks"
    assert "mock" in record["status"]


def test_serialize_is_json_safe(client):
    import json

    payload = serialize(client.turnovers_on(TOMORROW))
    # Should not raise — dates/enums already converted to primitives.
    json.dumps(payload)
    assert payload[0]["date"] == TOMORROW.isoformat()
