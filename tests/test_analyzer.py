from types import SimpleNamespace as NS

import pytest

from would_it_sell import AnalysisError, analyze
from would_it_sell.cli import render
from would_it_sell.models import Assessment, DimensionScore, overall_score, verdict_for


def make_assessment(**scores) -> Assessment:
    dims = scores or dict(market_demand=8, monetization=8, competition=6, feasibility=6, timing=6)
    return Assessment(
        idea_summary="Subscription meal-prep kits for gym-goers",
        category="business",
        dimensions=[DimensionScore(name=n, score=s, rationale="evidence") for n, s in dims.items()],
        target_customers=["Gym members aged 20-40"],
        competitors=[{"name": "Factor", "note": "$11/meal, national"}],
        pricing_benchmark="$9-13 per meal",
        revenue_model="Weekly subscription",
        key_risks=["Food cost inflation"],
        validation_steps=["Pre-sell 20 weekly boxes at one gym"],
        confidence="medium",
        sources=[{"title": "Example", "url": "https://example.com"}],
    )


class FakeMessages:
    def __init__(self, create_responses, parse_response):
        self.create_responses = list(create_responses)
        self.parse_response = parse_response
        self.create_calls = []
        self.parse_calls = []

    def create(self, **kwargs):
        self.create_calls.append(kwargs)
        return self.create_responses.pop(0)

    def parse(self, **kwargs):
        self.parse_calls.append(kwargs)
        return self.parse_response


def fake_client(create_responses, parse_response):
    return NS(beta=NS(messages=FakeMessages(create_responses, parse_response)))


def research_response(stop_reason="end_turn"):
    return NS(
        stop_reason=stop_reason,
        content=[
            NS(type="server_tool_use"),
            NS(type="web_search_tool_result", content=[
                NS(url="https://a.com", title="A"),
                NS(url="https://a.com", title="A dup"),
                NS(url="https://b.com", title=None),
            ]),
            NS(type="web_search_tool_result", content=NS(error_code="max_uses_exceeded")),
            NS(type="text", text="Demand is strong. Factor charges $11/meal."),
        ],
    )


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


def test_analyze_runs_research_then_assessment():
    client = fake_client([research_response()], NS(stop_reason="end_turn", parsed_output=make_assessment()))
    report = analyze("Meal prep kits for gym-goers", context="Austin, $5k budget", client=client)

    assert report.overall_score == 71
    assert report.verdict == "promising"

    research_call = client.beta.messages.create_calls[0]
    assert research_call["tools"][0]["type"] == "web_search_20260209"
    assert "Austin, $5k budget" in research_call["messages"][0]["content"]

    assess_prompt = client.beta.messages.parse_calls[0]["messages"][0]["content"]
    assert "Factor charges $11/meal" in assess_prompt
    assert "A: https://a.com" in assess_prompt
    assert "https://b.com: https://b.com" in assess_prompt
    assert assess_prompt.count("https://a.com") == 1


def test_pause_turn_is_resumed():
    paused = NS(stop_reason="pause_turn", content=[NS(type="server_tool_use")])
    client = fake_client([paused, research_response()], NS(stop_reason="end_turn", parsed_output=make_assessment()))
    analyze("idea", client=client)

    calls = client.beta.messages.create_calls
    assert len(calls) == 2
    assert calls[1]["messages"][1] == {"role": "assistant", "content": paused.content}


def test_refusal_raises():
    refused = NS(stop_reason="refusal", content=[], stop_details=NS(explanation="policy"))
    with pytest.raises(AnalysisError, match="policy"):
        analyze("idea", client=fake_client([refused], None))


def test_unparsed_assessment_raises():
    client = fake_client([research_response()], NS(stop_reason="max_tokens", parsed_output=None))
    with pytest.raises(AnalysisError, match="max_tokens"):
        analyze("idea", client=client)


def test_empty_idea_rejected():
    with pytest.raises(ValueError):
        analyze("   ", client=fake_client([], None))


def test_render_contains_key_sections():
    client = fake_client([research_response()], NS(stop_reason="end_turn", parsed_output=make_assessment()))
    text = render(analyze("idea", client=client))
    assert "Overall score: 71/100" in text
    assert "PROMISING" in text
    assert "Pre-sell 20 weekly boxes" in text
    assert "[########..] 8/10" in text
