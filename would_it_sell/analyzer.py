"""One-shot analysis: a single chat turn that must end with a recorded scorecard."""

from __future__ import annotations

from collections.abc import Callable

import anthropic

from .chat import ChatSession
from .models import Report

ONE_SHOT_PROMPT = """Evaluate this idea fully in one go. I can't answer follow-up questions, so make \
reasonable assumptions and state them. Research it with your tools, then call record_scorecard, \
then give a short summary.

Idea: {idea}"""


class AnalysisError(RuntimeError):
    pass


def analyze(
    idea: str,
    *,
    context: str | None = None,
    client: anthropic.Anthropic | None = None,
    on_event: Callable[[dict], None] | None = None,
) -> Report:
    """Research an idea with live data and return a scored Report."""
    idea = idea.strip()
    if not idea:
        raise ValueError("Describe the idea you want to evaluate.")
    session = ChatSession(client)
    prompt = ONE_SHOT_PROMPT.format(idea=idea)
    if context:
        prompt += f"\n\nExtra context: {context}"

    # The model can't be forced to call a tool, so nudge it once if it skipped the scorecard.
    for message in (prompt, "Please record the scorecard now with record_scorecard."):
        for event in session.send(message):
            if event["type"] == "error":
                raise AnalysisError(event["text"])
            if on_event:
                on_event(event)
        if session.latest_report:
            return session.latest_report
    raise AnalysisError("The analyst did not produce a scorecard.")
