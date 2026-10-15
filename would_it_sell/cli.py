"""Command-line entry point.

    would-it-sell                 chat in the terminal (default)
    would-it-sell web             open the chat in your browser
    would-it-sell "idea" [--json] one-shot scored report
"""

from __future__ import annotations

import argparse
import sys

import anthropic

from .analyzer import AnalysisError, analyze
from .chat import ChatSession, describe_api_error
from .models import DIMENSION_WEIGHTS, Report

VERDICT_LABELS = {
    "strong_go": "STRONG GO - evidence says people will pay for this",
    "promising": "PROMISING - worth validating with real customers",
    "risky": "RISKY - real weaknesses to solve first",
    "unlikely": "UNLIKELY - the market evidence is against it",
}

CHAT_HELP = "Commands: /new starts over, /score shows the last scorecard, /quit exits."


def _style(code: str):
    on = sys.stdout.isatty()
    return lambda s: f"\033[{code}m{s}\033[0m" if on else s


dim, bold, cyan = _style("2"), _style("1"), _style("36")


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


def print_event(event: dict) -> None:
    kind = event["type"]
    if kind == "text":
        print(event["text"], end="", flush=True)
    elif kind == "status":
        print(dim(f"\n  ... {event['text']}"), flush=True)
    elif kind == "scorecard":
        print("\n\n" + cyan(render(Report.model_validate(event["report"]))) + "\n", flush=True)
    elif kind == "error":
        print(f"\n[{event['text']}]", flush=True)


def chat_loop(session: ChatSession | None = None) -> int:
    session = session or ChatSession()
    print(bold("Would it Sell?") + " Tell me your idea, and we'll find out together.")
    print(dim(CHAT_HELP))
    while True:
        try:
            text = input("\n" + bold("You > ")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not text:
            continue
        if text in ("/quit", "/exit"):
            return 0
        if text == "/new":
            session = ChatSession(session.client, effort=session.effort)
            print(dim("Started a new conversation."))
            continue
        if text == "/score":
            print(render(session.latest_report) if session.latest_report else "No scorecard yet.")
            continue
        print("\n" + bold("Analyst > "), end="", flush=True)
        try:
            for event in session.send(text):
                print_event(event)
        except KeyboardInterrupt:
            print(dim("\n[interrupted - that message was discarded]"))
        except anthropic.APIError as e:
            print(f"\n[error: {describe_api_error(e)}]")
        print()


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv

    if not argv or argv[0] == "chat":
        return chat_loop()

    if argv[0] == "web":
        parser = argparse.ArgumentParser(prog="would-it-sell web", description="Chat in your browser.")
        parser.add_argument("--host", default="127.0.0.1")
        parser.add_argument("--port", type=int, default=8000)
        parser.add_argument("--no-browser", action="store_true", help="Don't open a browser tab.")
        args = parser.parse_args(argv[1:])
        from .web import serve
        serve(args.host, args.port, open_browser=not args.no_browser)
        return 0

    parser = argparse.ArgumentParser(
        prog="would-it-sell",
        description="Chat with an AI market analyst (no arguments), open the web app ('web'), "
                    "or get a one-shot report for an idea.",
    )
    parser.add_argument("idea", help="The idea to evaluate. Use '-' to read it from stdin.")
    parser.add_argument("--context", help="Extra details: budget, location, target market, your skills.")
    parser.add_argument("--json", action="store_true", help="Print the full report as JSON.")
    args = parser.parse_args(argv)

    idea = sys.stdin.read() if args.idea == "-" else args.idea
    print("Researching the market... this can take a few minutes.", file=sys.stderr)

    def progress(event: dict) -> None:
        if event["type"] == "status":
            print(f"  ... {event['text']}", file=sys.stderr, flush=True)

    try:
        report = analyze(idea, context=args.context, on_event=progress)
    except (AnalysisError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except anthropic.APIError as e:
        print(f"error: {describe_api_error(e)}", file=sys.stderr)
        return 1

    print(report.model_dump_json(indent=2) if args.json else render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
