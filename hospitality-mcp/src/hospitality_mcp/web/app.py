"""FastAPI app: a password-protected chat UI over the turnover briefing.

Auth is intentionally simple — one shared password for all staff, held in a
signed session cookie. Set these env vars:

  HOSPITABLE_ACCESS_TOKEN  Hospitable Personal Access Token (pat:read)
  ANTHROPIC_API_KEY        Anthropic API key (for Claude)
  APP_PASSWORD             the shared login password for staff
  SESSION_SECRET           random string used to sign the session cookie
  PORT                     optional, defaults to 8000
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.middleware.sessions import SessionMiddleware

# Load a local .env (gitignored) so credentials/password can live in one file
# and the app runs with no manual env setup.
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from . import chat, store

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Turnover Briefing")

_session_secret = os.environ.get("SESSION_SECRET")
if not _session_secret:
    _session_secret = secrets.token_urlsafe(32)
    print(
        "WARNING: SESSION_SECRET not set — using an ephemeral secret. "
        "Logins will not survive a restart. Set SESSION_SECRET in production."
    )
# In production (behind the host's HTTPS) set COOKIE_SECURE=true so the session
# cookie is only sent over HTTPS. Left off by default for local http:// dev.
_cookie_secure = os.environ.get("COOKIE_SECURE", "").lower() in ("1", "true", "yes")
app.add_middleware(
    SessionMiddleware,
    secret_key=_session_secret,
    https_only=_cookie_secure,
    same_site="lax",
)


def _is_authed(request: Request) -> bool:
    return bool(request.session.get("auth"))


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/me")
def me(request: Request) -> JSONResponse:
    return JSONResponse({"authenticated": _is_authed(request)})


@app.post("/api/login")
async def login(request: Request) -> JSONResponse:
    body = await request.json()
    expected = os.environ.get("APP_PASSWORD")
    if not expected:
        raise HTTPException(status_code=500, detail="APP_PASSWORD is not configured.")
    if not secrets.compare_digest(str(body.get("password", "")), expected):
        return JSONResponse({"error": "Incorrect password."}, status_code=401)
    request.session["auth"] = True
    return JSONResponse({"authenticated": True})


@app.post("/api/logout")
def logout(request: Request) -> JSONResponse:
    request.session.clear()
    return JSONResponse({"authenticated": False})


@app.get("/api/conversations")
def list_conversations(request: Request) -> JSONResponse:
    if not _is_authed(request):
        raise HTTPException(status_code=401, detail="Not authenticated.")
    return JSONResponse({"conversations": store.list_conversations()})


@app.get("/api/conversations/{cid}")
def get_conversation(request: Request, cid: str) -> JSONResponse:
    if not _is_authed(request):
        raise HTTPException(status_code=401, detail="Not authenticated.")
    convo = store.get_conversation(cid)
    if convo is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return JSONResponse(convo)


@app.delete("/api/conversations/{cid}")
def delete_conversation(request: Request, cid: str) -> JSONResponse:
    if not _is_authed(request):
        raise HTTPException(status_code=401, detail="Not authenticated.")
    return JSONResponse({"deleted": store.delete_conversation(cid)})


@app.post("/api/chat")
async def chat_endpoint(request: Request) -> StreamingResponse:
    if not _is_authed(request):
        raise HTTPException(status_code=401, detail="Not authenticated.")
    body = await request.json()
    messages = body.get("messages") or []
    conversation_id = body.get("conversation_id")

    async def event_stream():
        assistant_text = ""
        try:
            async for chunk in chat.stream_reply(messages):
                assistant_text += chunk
                yield f"data: {json.dumps({'text': chunk})}\n\n"
        except Exception as exc:  # surface failures to the UI instead of a silent hang
            yield f"data: {json.dumps({'error': str(exc)})}\n\n"
        # Persist the full turn (history + this reply) to the master JSON store
        # so the conversation and its context survive reloads and restarts.
        full = list(messages)
        if assistant_text.strip():
            full.append({"role": "assistant", "content": assistant_text})
        try:
            convo = await asyncio.to_thread(store.save_conversation, conversation_id, full)
            yield f"data: {json.dumps({'conversation_id': convo['id'], 'title': convo['title']})}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'error': f'failed to save conversation: {exc}'})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def main() -> None:
    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
