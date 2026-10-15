"""Command-line entry point: `would-it-sell "your idea"`."""

from __future__ import annotations

import argparse
import sys

import anthropic

from .analyzer import AnalysisError, analyze
from .models import DIMENSION_WEIGHTS, Report

VERDICT_LABELS = {
    "strong_go": "STRONG GO - evidence says people will pay for this",
    "promising": "PROMISING - worth validating with real customers",
    "risky": "RISKY - real weaknesses to solve first",
    "unlikely": "UNLIKELY - the market evidence is against it",
}


def render(report: Report) -> str:
    a = report.assessment
    lines = [
        f"Idea: {a.idea_summary}",
        f"Category: {a.category.replace('_', ' ')}",
        "",
        f"Overall score: {report.overall_score}/100",
        f"Verdict: {VERDICT_LABELS[report.verdict]}",
        f"Confidence: {a.confidence}",
        "",
        "Scores:",
    ]
    for d in sorted(a.dimensions, key=lambda d: -DIMENSION_WEIGHTS.get(d.name, 0)):
        score = max(0, min(10, d.score))
        bar = "#" * score + "." * (10 - score)
        lines.append(f"  {d.name.replace('_', ' '):<14} [{bar}] {score}/10  {d.rationale}")

    def section(title: str, items: list[str]) -> None:
        if items:
            lines.extend(["", f"{title}:"] + [f"  - {item}" for item in items])

    section("Target customers", a.target_customers)
    section("Competitors", [f"{c.name}: {c.note}" for c in a.competitors])
    lines.extend(["", f"Pricing benchmark: {a.pricing_benchmark}", f"Revenue model: {a.revenue_model}"])
    section("Key risks", a.key_risks)
    section("Validate next (cheap experiments)", a.validation_steps)
    section("Sources", [f"{s.title} - {s.url}" for s in a.sources])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="would-it-sell",
        description="Research an idea on the live web and estimate whether it can make money.",
    )
    parser.add_argument("idea", nargs="?", help="The idea to evaluate. Reads stdin if omitted.")
    parser.add_argument("--context", help="Extra details: budget, location, target market, your skills.")
    parser.add_argument("--max-searches", type=int, default=8, help="Web searches allowed (default 8).")
    parser.add_argument("--json", action="store_true", help="Print the full report as JSON.")
    args = parser.parse_args(argv)

    idea = args.idea if args.idea is not None else sys.stdin.read()
    if not idea.strip():
        parser.error("describe the idea as an argument or on stdin")

    print("Researching the market... this can take a minute or two.", file=sys.stderr)
    try:
        report = analyze(idea, context=args.context, max_searches=args.max_searches)
    except (AnalysisError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except anthropic.AuthenticationError:
        print("error: no valid Anthropic credentials. Set ANTHROPIC_API_KEY.", file=sys.stderr)
        return 1
    except anthropic.APIConnectionError:
        print("error: could not reach the Anthropic API. Check your connection.", file=sys.stderr)
        return 1
    except anthropic.APIStatusError as e:
        print(f"error: Anthropic API returned {e.status_code}: {e.message}", file=sys.stderr)
        return 1

    print(report.model_dump_json(indent=2) if args.json else render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
