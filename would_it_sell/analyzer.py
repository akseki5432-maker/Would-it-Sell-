"""Two-phase idea analysis with Claude.

1. Research: Claude uses live web search to gather market evidence
   (demand signals, competitors, pricing, trends, regulation).
2. Assess: Claude turns those notes into a structured, scored Assessment.

The overall score and verdict are computed deterministically from the
per-dimension scores so the number is explainable.
"""

from __future__ import annotations

import anthropic

from .models import Assessment, Report, Source, overall_score, verdict_for

MODEL = "claude-opus-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_CONTINUATIONS = 5

RESEARCH_SYSTEM = """You are a sharp, skeptical market analyst. Someone wants to know whether an idea \
(a business, a physical product, a software tool or a service) can make money.

Use web search to collect current, concrete evidence. Cover:
- Demand: search trends, communities and forums discussing the problem, reviews complaining about existing options.
- Competition: who already sells this or a substitute, their pricing, funding, traction.
- Monetization: what customers pay today, plausible margins, unit costs for physical goods.
- Feasibility: what it takes to build, make or deliver, and any regulation or licensing.
- Timing: is the market growing, shrinking, or being disrupted right now?

Prefer recent sources and real numbers. Say plainly when evidence is thin or contradictory. \
Finish with organized research notes, citing the URL behind each claim."""

ASSESS_SYSTEM = """You turn market research notes into an honest viability assessment.

Score each of these five dimensions exactly once, 0-10:
- market_demand: how many people have this problem and actively look for a solution.
- competition: 10 = open market or weak incumbents, 0 = saturated with strong, cheap players.
- monetization: willingness to pay and achievable margins.
- feasibility: how realistic it is for a small team or solo founder to build and deliver.
- timing: whether trends favor launching now.

Ground every score in the notes; do not invent facts or sources. If the notes are thin, \
score conservatively and set confidence to low. Only list sources whose URLs appear in the notes."""


def _research(client: anthropic.Anthropic, idea: str, context: str | None, max_searches: int):
    prompt = f"Idea to evaluate:\n{idea}"
    if context:
        prompt += f"\n\nExtra context from the founder:\n{context}"
    messages: list[dict] = [{"role": "user", "content": prompt}]
    tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": max_searches}]

    for _ in range(MAX_CONTINUATIONS + 1):
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            system=RESEARCH_SYSTEM,
            thinking={"type": "adaptive"},
            output_config={"effort": "high"},
            tools=tools,
            messages=messages,
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )
        _raise_on_refusal(response)
        if response.stop_reason != "pause_turn":
            break
        # The server-side search loop paused; resend so it resumes where it left off.
        messages = [messages[0], {"role": "assistant", "content": response.content}]

    notes = "\n".join(b.text for b in response.content if b.type == "text").strip()
    return notes, _collect_sources(response.content)


def _collect_sources(content) -> list[Source]:
    seen: dict[str, Source] = {}
    for block in content:
        if block.type != "web_search_tool_result":
            continue
        # An error result's content is a single object, a success is a list.
        if not isinstance(block.content, list):
            continue
        for result in block.content:
            url = getattr(result, "url", None)
            if url and url not in seen:
                seen[url] = Source(title=getattr(result, "title", None) or url, url=url)
    return list(seen.values())


def _assess(client: anthropic.Anthropic, idea: str, notes: str, sources: list[Source]) -> Assessment:
    source_list = "\n".join(f"- {s.title}: {s.url}" for s in sources) or "(none)"
    response = client.beta.messages.parse(
        model=MODEL,
        max_tokens=16000,
        system=ASSESS_SYSTEM,
        thinking={"type": "adaptive"},
        output_config={"effort": "high"},
        output_format=Assessment,
        messages=[{
            "role": "user",
            "content": f"Idea:\n{idea}\n\nResearch notes:\n{notes}\n\nSources searched:\n{source_list}",
        }],
        betas=[FALLBACK_BETA],
        fallbacks="default",
    )
    _raise_on_refusal(response)
    if response.parsed_output is None:
        raise AnalysisError(f"Assessment could not be parsed (stop_reason={response.stop_reason}).")
    return response.parsed_output


def _raise_on_refusal(response) -> None:
    if response.stop_reason == "refusal":
        details = getattr(response, "stop_details", None)
        reason = getattr(details, "explanation", None) or "no explanation given"
        raise AnalysisError(f"The model declined to analyze this idea: {reason}")


class AnalysisError(RuntimeError):
    pass


def analyze(
    idea: str,
    *,
    context: str | None = None,
    max_searches: int = 8,
    client: anthropic.Anthropic | None = None,
) -> Report:
    """Research an idea on the live web and return a scored Report."""
    idea = idea.strip()
    if not idea:
        raise ValueError("Describe the idea you want to evaluate.")
    client = client or anthropic.Anthropic()

    notes, sources = _research(client, idea, context, max_searches)
    if not notes:
        raise AnalysisError("Research phase returned no notes.")
    assessment = _assess(client, idea, notes, sources)

    score = overall_score(assessment.dimensions)
    return Report(idea=idea, overall_score=score, verdict=verdict_for(score), assessment=assessment)
