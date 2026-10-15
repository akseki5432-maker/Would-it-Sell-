"""Browser chat UI: a small FastAPI app that streams replies over server-sent events."""

from __future__ import annotations

import json
import threading
import uuid
import webbrowser
from collections import OrderedDict
from collections.abc import Callable, Iterator
from pathlib import Path

import anthropic
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from ..chat import ChatSession, describe_api_error

STATIC = Path(__file__).parent / "static"
MAX_SESSIONS = 200


class ChatRequest(BaseModel):
    session_id: str
    message: str


class _Sessions:
    """In-memory conversations, oldest evicted first."""

    def __init__(self, factory: Callable[[], ChatSession]):
        self.factory = factory
        self.items: OrderedDict[str, tuple[ChatSession, threading.Lock]] = OrderedDict()
        self.lock = threading.Lock()

    def create(self) -> str:
        sid = uuid.uuid4().hex
        with self.lock:
            self.items[sid] = (self.factory(), threading.Lock())
            while len(self.items) > MAX_SESSIONS:
                self.items.popitem(last=False)
        return sid

    def get(self, sid: str) -> tuple[ChatSession, threading.Lock]:
        with self.lock:
            if sid not in self.items:
                raise HTTPException(404, "Conversation not found. Start a new chat.")
            self.items.move_to_end(sid)
            return self.items[sid]


def create_app(session_factory: Callable[[], ChatSession] | None = None) -> FastAPI:
    app = FastAPI(title="Would it Sell?")
    sessions = _Sessions(session_factory or ChatSession)

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.post("/api/sessions")
    def new_session() -> dict:
        return {"session_id": sessions.create()}

    @app.post("/api/chat")
    def chat(req: ChatRequest) -> StreamingResponse:
        session, busy = sessions.get(req.session_id)
        if not busy.acquire(blocking=False):
            raise HTTPException(409, "Still answering your previous message.")

        def events() -> Iterator[str]:
            try:
                for event in session.send(req.message):
                    yield f"data: {json.dumps(event)}\n\n"
            except anthropic.APIError as e:
                yield f"data: {json.dumps({'type': 'error', 'text': describe_api_error(e)})}\n\n"
                yield f"data: {json.dumps({'type': 'done'})}\n\n"
            finally:
                busy.release()

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    return app


def serve(host: str = "127.0.0.1", port: int = 8000, open_browser: bool = True) -> None:
    import uvicorn

    url = f"http://{host}:{port}"
    print(f"Would it Sell? is running at {url}  (Ctrl+C to stop)")
    if open_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    uvicorn.run(create_app(), host=host, port=port, log_level="warning")
