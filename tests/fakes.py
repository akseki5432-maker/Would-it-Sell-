"""A scripted stand-in for the Anthropic client's streaming API."""

from types import SimpleNamespace as NS


def text(t):
    return NS(type="text", text=t)


def tool_use(id, name, input):
    return NS(type="tool_use", id=id, name=name, input=input)


def message(content, stop_reason="end_turn"):
    return NS(content=content, stop_reason=stop_reason)


class FakeStream:
    def __init__(self, final):
        self.final = final

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        if isinstance(self.final, Exception):
            raise self.final
        for block in self.final.content:
            if block.type == "text":
                yield NS(type="text", text=block.text)
            yield NS(type="content_block_stop", content_block=block)

    def get_final_message(self):
        return self.final


class FakeClient:
    """Each stream() call returns the next scripted message (or raises a scripted exception)."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []
        self.beta = NS(messages=NS(stream=self._stream))

    def _stream(self, **kwargs):
        # Snapshot the history, since the session keeps mutating its list.
        self.calls.append({**kwargs, "messages": [dict(m) for m in kwargs["messages"]]})
        return FakeStream(self.responses.pop(0))


SCORECARD = {
    "idea_summary": "Subscription meal-prep kits for gym-goers",
    "category": "business",
    "dimensions": [
        {"name": "market_demand", "score": 8, "rationale": "r"},
        {"name": "monetization", "score": 8, "rationale": "r"},
        {"name": "competition", "score": 6, "rationale": "r"},
        {"name": "feasibility", "score": 6, "rationale": "r"},
        {"name": "timing", "score": 6, "rationale": "r"},
    ],
    "target_customers": ["Gym members aged 20-40"],
    "competitors": [{"name": "Factor", "note": "$11/meal"}],
    "pricing_benchmark": "$9-13 per meal",
    "revenue_model": "Weekly subscription",
    "key_risks": ["Food cost inflation"],
    "validation_steps": ["Pre-sell 20 weekly boxes at one gym"],
    "confidence": "medium",
    "sources": [{"title": "Example", "url": "https://example.com"}],
}
