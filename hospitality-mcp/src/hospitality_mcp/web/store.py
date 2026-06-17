"""Local persistence for chat conversations.

Everything we need to restore a conversation — its messages (the context
history Claude is given), title and timestamps — is kept in a single master
JSON file so chats survive page reloads and server restarts. The file holds
guest messages (PII), so it is gitignored; point ``CONVERSATIONS_FILE`` at a
secure location in production.

Shape of the master file::

    {
      "version": 1,
      "conversations": {
        "<uuid>": {
          "id": "<uuid>",
          "title": "First user message, trimmed",
          "created_at": "2026-06-12T10:00:00+00:00",
          "updated_at": "2026-06-12T10:05:00+00:00",
          "messages": [{"role": "user|assistant", "content": "..."}]
        }
      }
    }

Writes are atomic (temp file + ``os.replace``) and guarded by a process-wide
lock, which is enough for this single-process FastAPI app.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_LOCK = threading.RLock()
_DEFAULT_FILE = Path(__file__).resolve().parents[3] / "data" / "conversations.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _file() -> Path:
    p = Path(os.environ.get("CONVERSATIONS_FILE", _DEFAULT_FILE))
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _load() -> Dict[str, Any]:
    path = _file()
    if not path.exists():
        return {"version": 1, "conversations": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"version": 1, "conversations": {}}
    data.setdefault("version", 1)
    data.setdefault("conversations", {})
    return data


def _save(data: Dict[str, Any]) -> None:
    path = _file()
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _title_from(messages: List[Dict[str, Any]]) -> str:
    for m in messages:
        if m.get("role") == "user" and str(m.get("content", "")).strip():
            text = " ".join(str(m["content"]).split())
            return text[:60] + ("…" if len(text) > 60 else "")
    return "New conversation"


def _summary(c: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": c["id"],
        "title": c.get("title") or "New conversation",
        "created_at": c.get("created_at"),
        "updated_at": c.get("updated_at"),
        "message_count": len(c.get("messages", [])),
    }


def list_conversations() -> List[Dict[str, Any]]:
    """Lightweight list (no message bodies), newest activity first."""
    with _LOCK:
        convos = _load()["conversations"].values()
    return sorted(
        (_summary(c) for c in convos),
        key=lambda s: s.get("updated_at") or "",
        reverse=True,
    )


def get_conversation(cid: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        return _load()["conversations"].get(cid)


def save_conversation(cid: Optional[str], messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Create or update a conversation from the full message history.

    Passing a falsy / unknown ``cid`` creates a new conversation and returns it
    (with its freshly minted id). The title is derived once, from the first user
    message, and then kept stable.
    """
    with _LOCK:
        data = _load()
        convos = data["conversations"]
        if not cid or cid not in convos:
            cid = cid if (cid and cid not in convos) else uuid.uuid4().hex
            convos[cid] = {
                "id": cid,
                "title": _title_from(messages),
                "created_at": _now(),
                "updated_at": _now(),
                "messages": messages,
            }
        else:
            c = convos[cid]
            c["messages"] = messages
            c["updated_at"] = _now()
            if not c.get("title") or c["title"] == "New conversation":
                c["title"] = _title_from(messages)
        _save(data)
        return convos[cid]


def delete_conversation(cid: str) -> bool:
    with _LOCK:
        data = _load()
        existed = data["conversations"].pop(cid, None) is not None
        if existed:
            _save(data)
        return existed
