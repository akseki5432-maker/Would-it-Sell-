# Would it Sell?

**Chat with an AI market analyst** about your idea (a business, a physical product, an app or a
service). It researches **live market data**, talks it through with you over as many messages as you
like, and scores whether the idea can make money.

```
You > A mobile coffee cart for office parks in Austin, about $15k to start

Analyst > Love it. A couple of things matter a lot here, so let me dig in...
  ... Searching the web: Austin office park coffee cart permits
  ... Reading Reddit discussions: coffee cart business
  ... Pulling World Bank economic data: US
  ... Running the numbers
**Demand is real**: ...

[scorecard: 68/100 - PROMISING]

You > What if I sold to construction sites instead?
Analyst > Interesting pivot. Let me check...
```

## Two ways to chat

```bash
would-it-sell web    # opens a polished chat in your browser (recommended)
would-it-sell        # chat right in the terminal
```

In the browser you get formatted replies, a live log of what the analyst is researching, and a
visual **scorecard card** with a 0-100 score, a verdict, per-dimension bars, competitors, risks,
sources and cheap experiments to run.

Terminal commands: `/new` starts over, `/score` shows the last scorecard, `/quit` exits.

You can still get a one-shot report:

```bash
would-it-sell "Handmade ceramic planters on Etsy" --context "I own a kiln" --json > report.json
```

## Where the data comes from

The analyst decides which sources fit your idea and runs them in parallel:

| Source | What it tells you | Key needed? |
|---|---|---|
| **Web search** + **web fetch** | Competitors, their prices, reviews, regulation, news | No (included with Claude) |
| **Reddit** | Which communities discuss the problem, and how engaged they are | No |
| **Hacker News** | Tech-community attention, this year vs last year | No |
| **Wikipedia pageviews** | 24-month public-interest trend (a stable Google Trends proxy) | No |
| **GitHub** | How many open-source tools already do this (for software ideas) | No |
| **World Bank** | Population, GDP per capita, inflation and internet reach for a country | No |
| **Unit-economics calculator** | Margin, break-even volume, LTV:CAC, payback (exact math, no guessing) | No |

If one source is down, the analyst carries on with the others.

## How scoring works

Whenever the analyst has evaluated an idea, it records a scorecard. It gives each of five
dimensions a score from 0 to 10, and the **code, not the AI**, computes the overall score using
fixed weights. That way the number is always explainable:

| Dimension | Weight |
|---|---|
| Market demand | 30% |
| Monetization | 25% |
| Competition (10 = open market) | 15% |
| Feasibility | 15% |
| Timing | 15% |

Verdicts: **Strong go** ≥ 75, **Promising** ≥ 60, **Risky** ≥ 40, otherwise **Unlikely**.
Change the idea mid-conversation, and the analyst re-scores it.

> **Reality check:** nobody can predict success. This tool gathers and organizes market evidence
> fast. Treat the verdict as a hypothesis and test it with the cheap experiments it suggests.

## Setup

```bash
pip install -e ".[dev]"
export ANTHROPIC_API_KEY=sk-ant-...
would-it-sell web
```

Uses Claude Opus 5.5 with adaptive thinking, prompt caching (long chats stay cheap), and automatic
model fallback if a request is declined.

From Python:

```python
from would_it_sell.chat import ChatSession

chat = ChatSession()
for event in chat.send("A subscription box for indoor plant care"):
    if event["type"] == "text":
        print(event["text"], end="")
for event in chat.send("I'm in Berlin, budget 5k euros"):
    ...
print(chat.latest_report)
```

## Development

```bash
pytest
```

The tests use a scripted fake client and mocked HTTP, so they need no API key or network.

Project layout:

```
would_it_sell/
  chat.py        conversation engine: streaming, tool loop, scorecards
  sources.py     live data tools (Reddit, HN, Wikipedia, GitHub, World Bank, unit economics)
  models.py      scorecard schema and scoring weights
  analyzer.py    one-shot analysis built on the chat engine
  cli.py         terminal chat and commands
  web/           FastAPI server and the browser chat page
```

## Roadmap ideas

- Save conversations and compare several ideas side by side
- More sources: Google Trends, Amazon/Etsy listings, Product Hunt, app-store reviews
- Track predictions against real outcomes to calibrate the weights
