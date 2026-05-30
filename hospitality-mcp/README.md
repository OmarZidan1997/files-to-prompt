# hospitality-mcp

An [MCP](https://modelcontextprotocol.io) server for hospitality / vacation-rental
operations. It lets an LLM answer everyday operational questions such as:

> "Get me all the turnovers tomorrow and any important notes I need to know —
> upcoming and current guests, complaints, luggage, anything per turnover or
> check-in."

It ships with a **realistic mock dataset** (anchored to *today*, so "tomorrow"
always has data) behind a pluggable client, so you can run and demo it
immediately and wire it to a real PMS later **without changing any tool code**.

## What it does

The server exposes these tools:

| Tool | Purpose |
| --- | --- |
| `daily_briefing` | **The headline tool.** Full briefing for a day: turnovers, check-ins/outs, open complaints, luggage holds + prioritized alerts. |
| `list_turnovers` | Turnovers for a day, priority-sorted, each with cleaner + important notes. |
| `list_check_ins` / `list_check_outs` | Arrivals / departures with guest + property detail. |
| `list_complaints` | Guest issues, severity-sorted (`open` / `in_progress` / `resolved` / `all`). |
| `list_luggage_holds` | Luggage currently being held. |
| `list_properties` / `get_property` | Property directory + detail (access code, wifi, parking). |
| `get_reservation` | One reservation with its guest and property. |
| `add_reservation_note` | Append an operational note to a reservation. |
| `send_guest_message` | Queue a guest message via the booking channel (mock: shows what *would* send). |

Day arguments accept `today`, `tomorrow`, a weekday (`friday`), an ISO date
(`2026-05-31`), or an offset (`+2`).

### A turnover, the way the briefing surfaces it

```
Sunset Villa [high] same_day=True  cleaner=Bright & Tidy Co.
   * Departing guest has a LATE checkout - cleaning starts later.
   * Departing guest is leaving luggage for later pickup.
   * Arriving guest requested EARLY check-in - tight cleaning window.
   * Arriving guest is a VIP - extra prep / welcome touch.
   * OPEN issue (low): Coffee maker leaking. Replace before next guest.
```

## Install & run

```bash
cd hospitality-mcp
pip install -e .            # installs the `mcp` SDK
hospitality-mcp            # runs the server over stdio
# or: python -m hospitality_mcp
```

### Backends

Selected via the `HOSPITALITY_BACKEND` env var:

| Backend | Data source | Extra env |
| --- | --- | --- |
| `mock` (default) | Bundled in-memory data, anchored to today | — |
| `hospitable` | Live [Hospitable Public API v2](https://developer.hospitable.com/docs/public-api-docs/) | `HOSPITABLE_ACCESS_TOKEN` |

Get a token from Hospitable: **Settings → API & Webhooks → Personal Access
Token** (requires a paid Host/Professional/Mogul plan). Then:

```bash
export HOSPITALITY_BACKEND=hospitable
export HOSPITABLE_ACCESS_TOKEN=hospitable_pat_xxx
hospitality-mcp
```

What maps to Hospitable, and what doesn't:

| Concept | Source in Hospitable |
| --- | --- |
| Properties, reservations, guests | `GET /properties`, `GET /reservations` (`include=guest,properties`) |
| Turnovers / same-day turnarounds | **Derived** from real check-in / check-out dates |
| Send guest message | `POST /reservations/{id}/messages` |
| Cleaners, complaints, luggage holds | **Not in the Hospitable API** — return empty for now |

### Use it from a client (e.g. Claude Code / Claude Desktop)

```json
{
  "mcpServers": {
    "hospitality": {
      "command": "hospitality-mcp",
      "env": {
        "HOSPITALITY_BACKEND": "hospitable",
        "HOSPITABLE_ACCESS_TOKEN": "hospitable_pat_xxx"
      }
    }
  }
}
```

Then ask: *"Use the hospitality tools to give me tomorrow's turnover briefing
and flag anything urgent."*

## Architecture

```
src/hospitality_mcp/
  server.py      FastMCP server + tool definitions
  client.py      HospitalityClient interface + MockClient
  mock_data.py   in-memory dataset, generated relative to today
  models.py      dataclasses (Property, Reservation, Turnover, Complaint, ...)
  dates.py       flexible day parsing ("tomorrow", "+2", ISO, weekday)
  hospitable.py  live Hospitable Public API v2 client (HospitalityClient)
```

### Going live / adding another PMS

`MockClient` and `HospitableClient` both implement the `HospitalityClient`
interface, and the turnover/briefing derivation lives in the base class — so a
backend only needs to supply the primitives (properties, reservations, guests,
check-ins/outs). To add another system (Guesty, Hostaway, a custom endpoint, …):

1. Add a subclass that makes HTTP calls and returns the same model objects
   (use `hospitable.py` as the template).
2. Wire it into `build_client()` in `server.py`, selected by
   `HOSPITALITY_BACKEND`.

No tool code changes — the tools depend only on the interface.

## Tests

```bash
pip install -e ".[test]"
pytest
```

The data-layer tests run without network access and don't require a live API.
