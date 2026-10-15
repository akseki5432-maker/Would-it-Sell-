"""Multi-turn chat with a market-research analyst.

ChatSession keeps the whole conversation. Each call to send() streams events
for one reply while Claude searches the web, calls the live data tools in
sources.py, and records scorecards as needed.

Event shapes yielded by send():
    {"type": "text", "text": "..."}          streamed reply text
    {"type": "status", "text": "..."}        what the analyst is doing right now
    {"type": "scorecard", "report": {...}}   a scored Report (models.Report)
    {"type": "error", "text": "..."}         the turn ended with a problem
    {"type": "done"}                         the reply is complete
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import anthropic

from .models import Assessment, Report, overall_score, verdict_for
from .sources import DATA_TOOLS, run_data_tool

MODEL = "claude-opus-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_STEPS = 25  # model calls per user message, a guard against runaway loops

SYSTEM_PROMPT = """You are "Would it Sell?", a friendly, candid market analyst. People bring you ideas: \
businesses, physical products, software tools, services. You help them figure out whether the idea can \
make money, through an ongoing conversation.

How you work:
- Talk like a sharp advisor, not a report generator. Keep replies focused and readable. Use short \
paragraphs and bullet points, and bold the key numbers.
- If an important detail is missing (where they'll sell, who the customer is, budget, price), ask one \
or two quick questions. If the idea is clear enough, start researching right away and ask while you share findings.
- Back claims with real evidence. Use web search and web fetch for competitors, prices, reviews, regulation \
and news. Use the data tools for hard numbers: reddit and hacker_news for demand conversations, \
wikipedia_interest for interest trends, github_projects for software competition, country_economics for \
the local market, and unit_economics whenever you talk about price, costs or profit. Run independent \
lookups in parallel. If a source fails, carry on with the others.
- Cite sources inline as markdown links.
- When you have evaluated an idea properly, call record_scorecard to record a structured scorecard. \
The app computes the overall 0-100 score from your dimension scores and shows it to the user as a card. \
After recording it, explain the verdict in plain words. Do not repeat the whole card. Record a new \
scorecard whenever the idea changes meaningfully.
- Be honest. Say clearly when the evidence points against an idea, and suggest pivots that could work. \
Say when the evidence is thin. Nobody can predict success; always end evaluations with cheap ways to \
test the idea with real customers.
- Reply in the language the user writes in."""


def _scorecard_tool() -> dict:
    schema = Assessment.model_json_schema()
    schema.pop("title", None)
    return {
        "name": "record_scorecard",
        "description": "Record the structured viability scorecard for the idea under discussion. Score each of "
                       "the five dimensions exactly once, 0-10 (competition: 10 = open market). Only list "
                       "sources you actually used. Returns the computed overall score and verdict.",
        "input_schema": schema,
        "eager_input_streaming": True,
    }


SERVER_TOOLS = [
    {"type": "web_search_20260209", "name": "web_search", "max_uses": 10},
    {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 6},
]

SERVER_TOOL_STATUS = {"web_search": "Searching the web", "web_fetch": "Reading a web page"}


class ChatSession:
    def __init__(self, client: anthropic.Anthropic | None = None, *, effort: str = "high"):
        self.client = client or anthropic.Anthropic()
        self.effort = effort
        self.messages: list[dict[str, Any]] = []
        self.reports: list[Report] = []
        self.tools = SERVER_TOOLS + [t.definition() for t in DATA_TOOLS.values()] + [_scorecard_tool()]

    @property
    def latest_report(self) -> Report | None:
        return self.reports[-1] if self.reports else None

    def send(self, text: str) -> Iterator[dict]:
        """Add a user message and stream the analyst's reply as events."""
        text = text.strip()
        if not text:
            yield {"type": "error", "text": "Type a message first."}
            return
        checkpoint = len(self.messages)
        self.messages.append({"role": "user", "content": text})
        try:
            yield from self._run_turn()
        except BaseException:
            # API error, Ctrl+C, or the consumer stopped listening (GeneratorExit):
            # roll back so the history stays valid and the user can simply retry.
            del self.messages[checkpoint:]
            raise
        yield {"type": "done"}

    def _run_turn(self) -> Iterator[dict]:
        json_retries = 0
        for _ in range(MAX_STEPS):
            try:
                response = yield from self._stream_once()
            except ValueError:
                # Tool input JSON the SDK could not parse at all: re-issue the request.
                json_retries += 1
                if json_retries > 2:
                    yield {"type": "error", "text": "The reply came back malformed. Please try again."}
                    self._drop_dangling_assistant()
                    return
                continue
            json_retries = 0

            if response.stop_reason == "refusal":
                self._drop_dangling_assistant()
                yield {"type": "error", "text": "The analyst declined to continue with that request."}
                return

            content = _echoable_content(response.content)
            self._append_assistant(content)

            if response.stop_reason == "pause_turn":
                continue  # server-side search loop paused; resend and it resumes

            tool_uses = [b for b in content if b.type == "tool_use"]
            if not tool_uses:
                if response.stop_reason == "max_tokens":
                    yield {"type": "error", "text": "The reply hit the length limit and was cut short."}
                return
            if response.stop_reason == "max_tokens":
                # A truncated tool input can look valid. Never run it.
                self._drop_dangling_assistant()
                yield {"type": "error", "text": "The reply was cut short. Please ask again."}
                return

            results = []
            for block in tool_uses:
                result, events = self._run_tool(block)
                yield from events
                results.append(result)
            self.messages.append({"role": "user", "content": results})

        yield {"type": "error", "text": "Stopped after too many research steps. Ask me to continue."}

    def _stream_once(self):
        with self.client.beta.messages.stream(
            model=MODEL,
            max_tokens=64000,
            system=SYSTEM_PROMPT,
            thinking={"type": "adaptive"},
            output_config={"effort": self.effort},
            tools=self.tools,
            messages=self.messages,
            cache_control={"type": "ephemeral"},
            betas=[FALLBACK_BETA],
            fallbacks="default",
        ) as stream:
            for event in stream:
                if event.type == "text":
                    yield {"type": "text", "text": event.text}
                elif event.type == "content_block_stop":
                    block = event.content_block
                    if block.type == "server_tool_use":
                        yield {"type": "status", "text": _server_tool_status(block)}
            return stream.get_final_message()

    def _run_tool(self, block) -> tuple[dict, list[dict]]:
        if block.name == "record_scorecard":
            return self._record_scorecard(block)
        if block.name not in DATA_TOOLS:
            return _tool_result(block.id, {"error": f"Unknown tool {block.name}"}, True), []
        events = [{"type": "status", "text": DATA_TOOLS[block.name].status + _detail(block.input)}]
        result, is_error = run_data_tool(block.name, block.input)
        return _tool_result(block.id, result, is_error), events

    def _record_scorecard(self, block) -> tuple[dict, list[dict]]:
        try:
            assessment = Assessment.model_validate(block.input)
        except Exception as e:
            return _tool_result(block.id, {"INVALID_INPUT": str(e)}, True), []
        score = overall_score(assessment.dimensions)
        idea = assessment.idea_summary
        report = Report(idea=idea, overall_score=score, verdict=verdict_for(score), assessment=assessment)
        self.reports.append(report)
        result = {"overall_score": score, "verdict": report.verdict,
                  "note": "The user now sees the scorecard card. Explain the verdict briefly."}
        return _tool_result(block.id, result, False), [{"type": "scorecard", "report": report.model_dump()}]

    def _append_assistant(self, content: list) -> None:
        # A resumed pause_turn continues the same assistant turn, so merge.
        if self.messages and self.messages[-1]["role"] == "assistant":
            self.messages[-1]["content"] = list(self.messages[-1]["content"]) + list(content)
        else:
            self.messages.append({"role": "assistant", "content": list(content)})

    def _drop_dangling_assistant(self) -> None:
        """Leave the history ending on a user turn's answer so the next send() is valid."""
        while self.messages and self.messages[-1]["role"] == "assistant" and any(
                getattr(b, "type", None) == "tool_use" for b in self.messages[-1]["content"]):
            self.messages.pop()
        if self.messages and self.messages[-1]["role"] == "user":
            # Close the turn so the next user message doesn't follow a user message.
            self.messages.append({"role": "assistant", "content": [
                {"type": "text", "text": "(This reply could not be completed.)"}]})


def _echoable_content(content: list) -> list:
    """After a mid-output model fallback, drop pre-boundary blocks the API says not to echo back."""
    boundary = max((i for i, b in enumerate(content) if b.type == "fallback"), default=None)
    if boundary is None:
        return list(content)
    result_ids = {getattr(b, "tool_use_id", None) for b in content if b.type.endswith("_tool_result")}
    kept = []
    for i, b in enumerate(content):
        if i < boundary:
            if b.type in ("thinking", "redacted_thinking", "tool_use"):
                continue
            if b.type == "server_tool_use" and b.id not in result_ids:
                continue
            if b.type not in ("text", "server_tool_use") and not b.type.endswith("_tool_result"):
                continue
        kept.append(b)
    return kept


def _tool_result(tool_use_id: str, result: dict, is_error: bool) -> dict:
    out = {"type": "tool_result", "tool_use_id": tool_use_id, "content": json.dumps(result)}
    if is_error:
        out["is_error"] = True
    return out


def _server_tool_status(block) -> str:
    label = SERVER_TOOL_STATUS.get(block.name, "Researching")
    return label + _detail(block.input)


def _detail(tool_input: Any) -> str:
    if isinstance(tool_input, dict):
        for key in ("query", "topic", "url", "country_code"):
            if isinstance(tool_input.get(key), str):
                return f": {tool_input[key]}"
    return ""


def describe_api_error(e: anthropic.APIError) -> str:
    if isinstance(e, anthropic.AuthenticationError):
        return "No valid Anthropic credentials. Set ANTHROPIC_API_KEY."
    if isinstance(e, anthropic.RateLimitError):
        return "Rate limited by the Anthropic API. Wait a moment and try again."
    if isinstance(e, anthropic.APIConnectionError):
        return "Could not reach the Anthropic API. Check your connection."
    if isinstance(e, anthropic.APIStatusError):
        return f"Anthropic API returned {e.status_code}: {e.message}"
    return str(e)
