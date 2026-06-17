# hospitality-mcp

An [MCP](https://modelcontextprotocol.io) server for hospitality / vacation-rental
operations, built around **Hospitable**. It lets an LLM answer everyday
operational questions such as:

> "Get me all the turnovers tomorrow and any important notes I need to know —
> upcoming and current guests, complaints, luggage, anything per turnover or
> check-in."

There are three ways to run it:

1. **Web app (for staff)** — `hospitality-web` is a password-protected chat
   website where in-house / cleaning staff get an LLM-analyzed turnover prep
   sheet and can ask open-ended questions. **This is the main thing most people
   want — see [Web app for staff](#web-app-for-staff) for a copy-paste quick
   start.**
2. **Gateway** — `hospitable-gateway` connects to Hospitable's own hosted MCP
   server, **re-exposes all of its tools**, and **adds** our custom composite
   tools (most importantly `daily_turnover_briefing`). One endpoint, every
   Hospitable tool *plus* the turnover briefing. ✅ verified live against a real
   Hospitable account.
3. **Standalone** — `hospitality-mcp` runs just our custom tools against either
   bundled **mock** data or the Hospitable Public API directly.

## Web app for staff

A simple website (one shared password) with a chat box and a **"Tomorrow's
turnover briefing"** button. Claude (`claude-opus-4-8`) can use:

- the custom **turnover briefing** (reads full guest conversations → prioritized
  prep sheet: same-day turnarounds first, plus genuine heads-up notes — damage to
  fix, restock, early/late check-in, extra guests, luggage, complaints — grounded
  in what guests actually wrote), and
- **Hospitable's full read-only toolset** (properties, reservations, messages,
  **reviews**, tasks, calendar, upsells, payouts, …) plus `create-task`.

So staff can ask open-ended questions — e.g. *"for each flat, pull the reviews,
flag the recurring problems with how many guests mentioned each, translate any
foreign-language comments, and give me a list of things to fix or buy."* Write /
guest-facing actions (sending messages, responding to reviews, cancelling,
smart-locks) are intentionally **not** exposed; if a request needs one, the bot
says it can't do it.

### Run it locally (5 steps)

You need **Python 3.10+** and the four secrets in the table below (a Hospitable
account + an Anthropic API key).

```bash
# 1. Get the code and enter it
cd hospitality-mcp

# 2. Create an isolated environment (avoids the "externally-managed-environment"
#    pip error on modern macOS/Debian/Ubuntu) and install the web extras
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e ".[web]"

# 3. Create your config from the template
cp env.example .env

# 4. Edit .env and fill in the values (see the table below).
#    Tip — generate a strong SESSION_SECRET:
python3 -c "import secrets; print(secrets.token_hex(32))"

# 5. Start the server
hospitality-web                      # serves on http://localhost:8000
```

Then open **http://localhost:8000**, log in with your `APP_PASSWORD`, and click
**Tomorrow's turnover briefing** or just type a question.

> After the first install you only need steps **2 (`source .venv/bin/activate`)**
> and **5 (`hospitality-web`)** to start it again.

### Configuration (`.env`)

`.env` is gitignored and holds everything — no other manual setup:

| Var | Required | What | Where to get it |
| --- | --- | --- | --- |
| `HOSPITABLE_ACCESS_TOKEN` | ✅ | Hospitable Personal Access Token (`pat:read`) — used by the turnover briefing | Hospitable → Settings → API access |
| `HOSPITABLE_MCP_TOKEN` | ✅ | Hospitable bearer (`mcp:use`) — the full Hospitable toolset | Hospitable → AI agents → Fallback bearer tokens |
| `ANTHROPIC_API_KEY` | ✅ | Anthropic API key (powers Claude) | console.anthropic.com → API keys |
| `APP_PASSWORD` | ✅ | the shared staff login password (pick anything) | you choose it |
| `SESSION_SECRET` | ✅ | long random string that signs the login cookie | `python3 -c "import secrets; print(secrets.token_hex(32))"` |
| `PORT` | — | listen port (default `8000`) | optional |

### Deploy it (Docker)

The repo ships a `Dockerfile`. Secrets are **passed at run time**, never baked
into the image:

```bash
docker build -t turnover-web .
docker run --env-file .env -p 8000:8000 turnover-web
```

The app is now on `http://localhost:8000`. To run it in the background add
`-d --restart unless-stopped`.

### Deploy to Render (live, for staff)

The repo ships a `render.yaml` blueprint, so going live is mostly clicking
through a wizard. Render terminates HTTPS and gives a free
`https://<name>.onrender.com` URL — which is what makes the copy-to-clipboard
button work and keeps the login cookie secure.

1. **Push to GitHub** (a private repo is fine — secrets are never committed):
   ```bash
   git add -A && git commit -m "Add Render deploy config" && git push
   ```
2. **Create the service:** Render Dashboard → **New → Blueprint** → pick this
   repo. Render reads `render.yaml` and provisions one web service + a 1 GB
   persistent disk (mounted at `/data`, where the conversations JSON lives so
   chats survive redeploys).
3. **Fill in the secrets:** Render prompts for the four `sync: false` vars.
   Paste the **same values from your local `.env`**:
   `HOSPITABLE_ACCESS_TOKEN`, `HOSPITABLE_MCP_TOKEN`, `ANTHROPIC_API_KEY`,
   `APP_PASSWORD`. (`SESSION_SECRET`, `COOKIE_SECURE`, `CONVERSATIONS_FILE` and
   `PORT` are handled automatically — leave them alone.)
4. **Deploy.** First build takes a few minutes. When it's live, open the
   `onrender.com` URL, confirm you can log in with `APP_PASSWORD`, and generate
   a briefing.
5. **Share** the URL + `APP_PASSWORD` with your staff. To rotate the password
   later, change `APP_PASSWORD` in the Render dashboard and redeploy.

> **Note on plan:** `render.yaml` uses the `starter` plan because persistent
> disks (for saved conversations) aren't available on the free plan. If you
> don't need chat history to survive redeploys, you can switch to `free` and
> drop the `disk:` block — chats then live only in memory of the current
> container.

**Other hosts** (Fly.io, Railway, a plain VM) work the same way: point them at
the `Dockerfile`, set the env vars from the table above in their dashboard
(**never** upload `.env`), set `COOKIE_SECURE=true`, give `CONVERSATIONS_FILE` a
path on a persistent volume, and expose port `8000` (or whatever `PORT` they
require).

### Troubleshooting

| Symptom | Fix |
| --- | --- |
| `error: externally-managed-environment` on `pip install` | You skipped the venv — run step 2 (`python3 -m venv .venv && source .venv/bin/activate`). |
| `command not found: hospitality-web` | The venv isn't active — `source .venv/bin/activate`. |
| Login rejects the right password | `APP_PASSWORD` in `.env` has stray quotes/spaces, or the server was started before you edited `.env` — restart it. |
| Chat errors out / no reply | Check `ANTHROPIC_API_KEY` is set and valid. |
| Reviews / reservation tools "can't connect" | `HOSPITABLE_MCP_TOKEN` is missing or lacks `mcp:use`. The briefing still works on `HOSPITABLE_ACCESS_TOKEN` alone. |
| `GET stream disconnected, reconnecting…` in logs | Harmless — the MCP transport's notification channel reconnecting. Tool calls still succeed. |

## The gateway

```
AI assistant ──stdio──▶  hospitable-gateway  ──HTTP──▶  Hospitable MCP (59 tools)
                               │
                               └─ custom tools ──Public API v2──▶ Hospitable
```

It needs two credentials (see **Security** below — never commit these):

| Env var | What | Where in Hospitable |
| --- | --- | --- |
| `HOSPITABLE_MCP_TOKEN` | Bearer token, scope `mcp:use` | AI agents → Fallback bearer tokens |
| `HOSPITABLE_ACCESS_TOKEN` | Personal Access Token (`pat:read/write`) — used by the custom tools | Settings → API access |

```bash
pip install -e .
export HOSPITABLE_MCP_TOKEN=...      # mcp:use bearer
export HOSPITABLE_ACCESS_TOKEN=...   # Public API PAT
hospitable-gateway                   # stdio MCP server: 59 Hospitable tools + 2 custom
```

Add it to an MCP client:

```json
{
  "mcpServers": {
    "hospitable": {
      "command": "hospitable-gateway",
      "env": {
        "HOSPITABLE_MCP_TOKEN": "...",
        "HOSPITABLE_ACCESS_TOKEN": "..."
      }
    }
  }
}
```

Then ask: *"Give me the daily turnover briefing for tomorrow."* (calls the
custom tool), or use any native Hospitable tool like `get-reservations`,
`get-tasks`, `get-property-calendar`, `send-reservation-message`, etc.

### Custom tools added by the gateway

| Tool | Purpose |
| --- | --- |
| `daily_turnover_briefing` | One briefing for a day across all properties: turnovers, same-day turnarounds, check-ins/outs, complaints + luggage derived from genuine guest messages, prioritized alerts, and the **full genuine guest conversation** for each turnover reservation (`guest_conversations`) so the whole dialog can be analysed. |
| `list_turnovers` | Property turnovers for a day, priority-sorted, with notes. |

> Sourcing note: the custom tools read live data via the **Public API v2** using
> your PAT. Complaints, luggage holds and "things to be aware of" are derived
> from **genuine guest message dialog** only — AI auto-replies, automated
> templates and system messages (`source` of `AI` / `automated` / `hospitable`)
> are ignored. Scanning is scoped to the reservations turning over that day and
> deduped to one item per guest. Cleaning **tasks** are available as native
> upstream tools (`get-tasks` / `create-task`).

## Standalone server (mock or direct API)

The `hospitality-mcp` entry point runs the custom tools on their own and exposes
a fuller toolset:

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
| Complaints, luggage holds | **Derived** by scanning recent guest messages (`GET /reservations/{id}/messages`) for keywords — approximate, flagged `(auto-detected …)` |
| Cleaners | **Not in the Hospitable API** — empty |

Message scanning is on by default and tunable on `HospitableClient`
(`scan_messages`, `message_recency_days`, `max_scan_reservations`). Detected
complaints also feed into each turnover's notes and priority.

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
  gateway.py     Hospitable MCP gateway: proxy upstream tools + custom tools
  server.py      standalone FastMCP server (custom tools, mock/hospitable)
  briefing.py    composite views (daily_turnover_briefing, list_turnovers)
  client.py      HospitalityClient interface + MockClient + shared turnover logic
  hospitable.py  live Hospitable Public API v2 client (HospitalityClient)
  mock_data.py   in-memory dataset, generated relative to today
  models.py      dataclasses (Property, Reservation, Turnover, Complaint, ...)
  dates.py       flexible day parsing ("tomorrow", "+2", ISO, weekday)
```

## Security

This server takes long-lived Hospitable tokens. Treat them like passwords:

- **Never commit them.** They belong in environment variables or a secrets
  manager, not in the repo. `.gitignore` blocks common token/secret filenames.
- Prefer the **least privilege** that works (e.g. a read-only PAT if you don't
  need the write tools).
- **Rotate** a token immediately if it is ever pasted into a chat, log, or
  shared channel.

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
