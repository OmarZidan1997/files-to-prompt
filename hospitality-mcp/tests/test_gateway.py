"""Unit tests for the gateway's tool wiring (no network/credentials needed)."""

from hospitality_mcp.gateway import (
    CUSTOM_HANDLERS,
    CUSTOM_TOOLS,
    build_custom_tools,
)


def test_every_custom_tool_has_a_handler():
    for tool in CUSTOM_TOOLS:
        assert tool.name in CUSTOM_HANDLERS, f"no handler for {tool.name}"


def test_custom_tools_pass_through_when_no_collision():
    exposed, dispatch = build_custom_tools(upstream_names={"get-properties", "get-reservations"})
    names = {t.name for t in exposed}
    assert "daily_turnover_briefing" in names
    assert dispatch["daily_turnover_briefing"] == "daily_turnover_briefing"


def test_custom_tool_name_collision_is_prefixed():
    # If Hospitable ever ships a tool named "list_turnovers", ours is namespaced.
    exposed, dispatch = build_custom_tools(upstream_names={"list_turnovers"})
    names = {t.name for t in exposed}
    assert "custom_list_turnovers" in names
    assert "list_turnovers" not in names  # the upstream one is not shadowed
    assert dispatch["custom_list_turnovers"] == "list_turnovers"


def test_day_schema_is_optional():
    for tool in CUSTOM_TOOLS:
        assert tool.inputSchema["required"] == []
        assert "day" in tool.inputSchema["properties"]
