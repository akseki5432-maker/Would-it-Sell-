import json
from types import SimpleNamespace as NS

import anthropic
import httpx
import pytest

from would_it_sell import AnalysisError, analyze
from would_it_sell.chat import ChatSession, _echoable_content
from would_it_sell.cli import render
from would_it_sell.models import DimensionScore, Report, overall_score, verdict_for

from .fakes import SCORECARD, FakeClient, message, text, tool_use


def events_of(session, msg):
    return list(session.send(msg))


def test_overall_score_is_weighted_and_clamped():
    dims = [DimensionScore(name=n, score=10, rationale="") for n in
            ("market_demand", "monetization", "competition", "feasibility", "timing")]
    assert overall_score(dims) == 100
    assert overall_score([DimensionScore(name="market_demand", score=15, rationale="")]) == 30
    assert overall_score([]) == 0


@pytest.mark.parametrize("score,verdict", [(90, "strong_go"), (75, "strong_go"), (60, "promising"),
                                           (59, "risky"), (40, "risky"), (10, "unlikely")])
def test_verdict_bands(score, verdict):
    assert verdict_for(score) == verdict


def test_multi_turn_conversation_keeps_history():
    client = FakeClient(message([text("Where will you sell it?")]), message([text("Austin looks good.")]))
    session = ChatSession(client)

    first = events_of(session, "Meal prep kits for gym-goers")
    assert [e for e in first if e["type"] == "text"] == [{"type": "text", "text": "Where will you sell it?"}]
    assert first[-1] == {"type": "done"}

    events_of(session, "Austin, Texas")
    second_call = client.calls[1]["messages"]
    assert [m["role"] for m in second_call] == ["user", "assistant", "user"]
    assert second_call[2]["content"] == "Austin, Texas"
    assert len(session.messages) == 4


def test_request_uses_tools_caching_and_fallbacks():
    client = FakeClient(message([text("hi")]))
    events_of(ChatSession(client), "hello")
    call = client.calls[0]
    names = {t["name"] for t in call["tools"]}
    assert {"web_search", "web_fetch", "reddit", "hacker_news", "wikipedia_interest", "github_projects",
            "country_economics", "unit_economics", "record_scorecard"} <= names
    assert call["cache_control"] == {"type": "ephemeral"}
    assert call["fallbacks"] == "default"
    assert call["model"] == "claude-opus-5-5"


def test_data_tool_is_run_and_result_returned(monkeypatch):
    seen = {}

    def fake_run(name, raw, http=None):
        seen[name] = raw
        return {"posts_found_past_year": 12}, False

    monkeypatch.setattr("would_it_sell.chat.run_data_tool", fake_run)
    client = FakeClient(
        message([text("Let me check Reddit."), tool_use("t1", "reddit", {"query": "meal prep"})], "tool_use"),
        message([text("12 posts found.")]),
    )
    events = events_of(ChatSession(client), "idea")

    assert {"type": "status", "text": "Reading Reddit discussions: meal prep"} in events
    assert seen == {"reddit": {"query": "meal prep"}}
    tool_result = client.calls[1]["messages"][-1]["content"][0]
    assert tool_result["tool_use_id"] == "t1"
    assert json.loads(tool_result["content"]) == {"posts_found_past_year": 12}


def test_scorecard_is_recorded_scored_and_emitted():
    client = FakeClient(
        message([tool_use("s1", "record_scorecard", SCORECARD)], "tool_use"),
        message([text("Promising overall.")]),
    )
    session = ChatSession(client)
    events = events_of(session, "idea")

    card = next(e for e in events if e["type"] == "scorecard")
    assert card["report"]["overall_score"] == 71
    assert card["report"]["verdict"] == "promising"
    assert session.latest_report.overall_score == 71
    result = json.loads(client.calls[1]["messages"][-1]["content"][0]["content"])
    assert result["overall_score"] == 71


def test_invalid_scorecard_returns_error_to_model():
    client = FakeClient(
        message([tool_use("s1", "record_scorecard", {"idea_summary": "x"})], "tool_use"),
        message([text("Fixed.")]),
    )
    session = ChatSession(client)
    events_of(session, "idea")
    result = client.calls[1]["messages"][-1]["content"][0]
    assert result["is_error"] is True
    assert session.latest_report is None


def test_pause_turn_resumes_and_merges_assistant_turn():
    paused = [NS(type="server_tool_use", id="srv1", name="web_search", input={"query": "meal kits"})]
    client = FakeClient(message(paused, "pause_turn"), message([text("Done researching.")]))
    session = ChatSession(client)
    events = events_of(session, "idea")

    assert {"type": "status", "text": "Searching the web: meal kits"} in events
    assert client.calls[1]["messages"][-1]["role"] == "assistant"
    assert [m["role"] for m in session.messages] == ["user", "assistant"]
    assert len(session.messages[1]["content"]) == 2


def test_refusal_reports_error_and_keeps_history_valid():
    client = FakeClient(message([], "refusal"), message([text("Sure.")]))
    session = ChatSession(client)
    events = events_of(session, "idea")
    assert any(e["type"] == "error" for e in events)
    assert session.messages[-1]["role"] == "assistant"
    events_of(session, "another idea")
    assert [m["role"] for m in client.calls[1]["messages"]] == ["user", "assistant", "user"]


def test_truncated_tool_call_is_not_run(monkeypatch):
    monkeypatch.setattr("would_it_sell.chat.run_data_tool", lambda *a, **k: pytest.fail("ran tool"))
    client = FakeClient(message([tool_use("t1", "reddit", {"query": "me"})], "max_tokens"))
    session = ChatSession(client)
    events = events_of(session, "idea")
    assert any(e["type"] == "error" for e in events)
    assert all(m["role"] != "assistant" or not any(getattr(b, "type", "") == "tool_use" for b in m["content"])
               for m in session.messages)


def test_api_error_rolls_back_history():
    err = anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com"))
    client = FakeClient(message([text("ok")]), err)
    session = ChatSession(client)
    events_of(session, "first")
    with pytest.raises(anthropic.APIConnectionError):
        events_of(session, "second")
    assert len(session.messages) == 2


def test_abandoned_stream_rolls_back_history():
    client = FakeClient(message([text("a"), text("b")]))
    session = ChatSession(client)
    gen = session.send("idea")
    next(gen)
    gen.close()  # e.g. the browser tab closed mid-reply
    assert session.messages == []


def test_echoable_content_strips_pre_fallback_internals():
    content = [
        NS(type="thinking"), NS(type="text", text="partial"), NS(type="tool_use", id="x"),
        NS(type="server_tool_use", id="s1"), NS(type="web_search_tool_result", tool_use_id="s1"),
        NS(type="server_tool_use", id="s2"),
        NS(type="fallback"), NS(type="thinking"), NS(type="text", text="after"),
    ]
    kept = [b.type for b in _echoable_content(content)]
    assert kept == ["text", "server_tool_use", "web_search_tool_result", "fallback", "thinking", "text"]


def test_analyze_returns_report_and_nudges_once():
    client = FakeClient(
        message([text("Here is my take.")]),
        message([tool_use("s1", "record_scorecard", SCORECARD)], "tool_use"),
        message([text("Recorded.")]),
    )
    report = analyze("Meal prep kits", context="Austin", client=client)
    assert report.overall_score == 71
    assert "Austin" in client.calls[0]["messages"][0]["content"]
    assert "record_scorecard" in client.calls[1]["messages"][-1]["content"]


def test_analyze_without_scorecard_raises():
    client = FakeClient(message([text("a")]), message([text("b")]))
    with pytest.raises(AnalysisError):
        analyze("idea", client=client)


def test_render_contains_key_sections():
    from would_it_sell.models import Assessment
    a = Assessment.model_validate(SCORECARD)
    text_out = render(Report(idea="x", overall_score=71, verdict="promising", assessment=a))
    assert "Overall score: 71/100" in text_out
    assert "PROMISING" in text_out
    assert "[########..] 8/10" in text_out
