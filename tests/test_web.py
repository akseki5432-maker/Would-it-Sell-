import json

from fastapi.testclient import TestClient

from would_it_sell.chat import ChatSession
from would_it_sell.web import create_app

from .fakes import FakeClient, message, text


def parse_sse(body: str) -> list[dict]:
    return [json.loads(line[6:]) for line in body.split("\n\n") if line.startswith("data: ")]


def test_index_served():
    client = TestClient(create_app(lambda: ChatSession(FakeClient())))
    r = client.get("/")
    assert r.status_code == 200 and "Would it Sell?" in r.text


def test_chat_streams_events_across_turns():
    fake = FakeClient(message([text("What city?")]), message([text("Great, Austin.")]))
    client = TestClient(create_app(lambda: ChatSession(fake)))
    sid = client.post("/api/sessions").json()["session_id"]

    first = parse_sse(client.post("/api/chat", json={"session_id": sid, "message": "coffee cart"}).text)
    assert first == [{"type": "text", "text": "What city?"}, {"type": "done"}]

    second = parse_sse(client.post("/api/chat", json={"session_id": sid, "message": "Austin"}).text)
    assert second[0]["text"] == "Great, Austin."
    assert len(fake.calls[1]["messages"]) == 3


def test_unknown_session_is_404():
    client = TestClient(create_app(lambda: ChatSession(FakeClient())))
    assert client.post("/api/chat", json={"session_id": "nope", "message": "hi"}).status_code == 404
