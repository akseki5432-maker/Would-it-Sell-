# Would it Sell?

Describe an idea (a business, a physical product, a software tool or a service) and get an
evidence-based estimate of whether it can make money, built from **live web research**.

```
$ would-it-sell "Subscription meal-prep kits for gym-goers" --context "Austin TX, $5k budget, solo founder"

Overall score: 71/100
Verdict: PROMISING - worth validating with real customers
Confidence: medium

Scores:
  market demand  [########..] 8/10  ...
  monetization   [########..] 8/10  ...
  competition    [######....] 6/10  ...
  ...
Validate next (cheap experiments):
  - Pre-sell 20 weekly boxes at one gym
Sources:
  - ...
```

## How it works

1. **Research.** Claude runs live web searches on demand signals, competitors and their prices,
   costs, regulation and market trends, then writes cited research notes.
2. **Assess.** A second call turns those notes into a structured assessment. It scores five
   dimensions from 0 to 10 and lists target customers, competitors, a pricing benchmark, risks,
   cheap validation experiments and sources.
3. **Score.** The code (not the model) computes the 0-100 score as a weighted average, so the
   number is always explainable:

| Dimension     | Weight |
|---------------|--------|
| market_demand | 30%    |
| monetization  | 25%    |
| competition   | 15%    |
| feasibility   | 15%    |
| timing        | 15%    |

Verdicts: `strong_go` ≥ 75, `promising` ≥ 60, `risky` ≥ 40, otherwise `unlikely`.

> **Reality check:** no tool can actually predict success. This tool collects and organizes market
> evidence quickly. Treat the verdict as a hypothesis and test it with the validation steps it
> suggests, such as pre-sales, landing pages and customer interviews.

## Setup

```bash
pip install -e ".[dev]"
export ANTHROPIC_API_KEY=sk-ant-...
```

## Usage

```bash
would-it-sell "A smart pet feeder that detects which pet is eating"
would-it-sell "Mobile bike repair van" --context "Lisbon, $20k budget" --json > report.json
echo "AI bookkeeping for food trucks" | python -m would_it_sell
```

From Python:

```python
from would_it_sell import analyze

report = analyze("Handmade ceramic planters sold on Etsy", context="I already own a kiln")
print(report.overall_score, report.verdict)
```

## Development

```bash
pytest
```

The tests use a fake client, so they need no API key or network.

## Roadmap ideas

- Add direct data feeds (Google Trends, Reddit, Amazon/Etsy listings, Product Hunt) as extra signals
- A web UI where you compare several ideas side by side
- Track predictions against real outcomes to calibrate the weights
