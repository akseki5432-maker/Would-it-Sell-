"""Live market-data tools Claude can call during a conversation.

Every tool takes a validated pydantic input and returns a JSON-serializable dict.
All sources are free and need no API keys. A failing source returns an
{"error": ...} dict instead of raising, so one dead API never breaks a chat.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Callable
from urllib.parse import quote

import httpx
from pydantic import BaseModel, Field

USER_AGENT = "would-it-sell/0.2 (market research tool)"
TIMEOUT = 15.0


def _client() -> httpx.Client:
    return httpx.Client(timeout=TIMEOUT, headers={"User-Agent": USER_AGENT}, follow_redirects=True)


# --- Hacker News -------------------------------------------------------------

class HackerNewsInput(BaseModel):
    query: str = Field(description="Short keyword query, e.g. 'meal prep subscription'.")


def hacker_news(args: HackerNewsInput, http: httpx.Client) -> dict:
    """Tech-community attention: story counts this year vs last year, top stories."""
    url = "https://hn.algolia.com/api/v1/search"
    now = int(time.time())
    year = 365 * 24 * 3600

    def count(start: int, end: int) -> int:
        r = http.get(url, params={"query": args.query, "tags": "story", "hitsPerPage": 0,
                                  "numericFilters": f"created_at_i>{start},created_at_i<{end}"})
        r.raise_for_status()
        return r.json()["nbHits"]

    last_12m = count(now - year, now)
    prior_12m = count(now - 2 * year, now - year)
    r = http.get(url, params={"query": args.query, "tags": "story", "hitsPerPage": 5})
    r.raise_for_status()
    top = [
        {"title": h.get("title"), "points": h.get("points"), "comments": h.get("num_comments"),
         "date": (h.get("created_at") or "")[:10],
         "url": f"https://news.ycombinator.com/item?id={h['objectID']}"}
        for h in r.json()["hits"]
    ]
    return {"stories_last_12_months": last_12m, "stories_prior_12_months": prior_12m,
            "trend_pct": _pct_change(prior_12m, last_12m), "top_stories": top}


# --- Reddit ------------------------------------------------------------------

class RedditInput(BaseModel):
    query: str = Field(description="What people would say when discussing the problem or product.")


def reddit(args: RedditInput, http: httpx.Client) -> dict:
    """Consumer conversations: which communities talk about this, and how engaged they are."""
    r = http.get("https://www.reddit.com/search.json",
                 params={"q": args.query, "sort": "relevance", "t": "year", "limit": 25})
    r.raise_for_status()
    posts = [c["data"] for c in r.json()["data"]["children"]]
    subs: dict[str, int] = {}
    for p in posts:
        subs[p["subreddit"]] = subs.get(p["subreddit"], 0) + 1
    top = sorted(posts, key=lambda p: p.get("score", 0), reverse=True)[:6]
    return {
        "posts_found_past_year": len(posts),
        "total_upvotes": sum(p.get("score", 0) for p in posts),
        "total_comments": sum(p.get("num_comments", 0) for p in posts),
        "top_subreddits": sorted(subs, key=subs.get, reverse=True)[:6],
        "top_posts": [{"title": p["title"], "subreddit": p["subreddit"], "upvotes": p.get("score"),
                       "comments": p.get("num_comments"),
                       "url": f"https://www.reddit.com{p['permalink']}"} for p in top],
    }


# --- Wikipedia pageviews (a free, stable proxy for search interest) ----------

class WikipediaInput(BaseModel):
    topic: str = Field(description="A general topic with a Wikipedia article, e.g. 'Meal kit' or 'E-bike'.")
    language: str = Field(default="en", description="Wikipedia language code, e.g. 'en', 'pt', 'tr'.")


def wikipedia_interest(args: WikipediaInput, http: httpx.Client) -> dict:
    """Public interest over 24 months, measured by monthly Wikipedia article views."""
    lang = args.language.lower()[:3] or "en"
    s = http.get(f"https://{lang}.wikipedia.org/w/api.php",
                 params={"action": "opensearch", "search": args.topic, "limit": 1, "format": "json"})
    s.raise_for_status()
    titles = s.json()[1]
    if not titles:
        return {"error": f"No Wikipedia article found for '{args.topic}'. Try a broader topic."}
    title = titles[0]

    end = date.today().replace(day=1) - timedelta(days=1)  # last complete month
    start = (end.replace(day=1) - timedelta(days=700)).replace(day=1)
    r = http.get(
        "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
        f"{lang}.wikipedia/all-access/user/{quote(title.replace(' ', '_'), safe='')}/monthly/"
        f"{start:%Y%m%d}00/{end:%Y%m%d}00")
    r.raise_for_status()
    views = [item["views"] for item in r.json()["items"]]
    recent, earlier = views[-12:], views[-24:-12]
    avg_recent = round(sum(recent) / len(recent)) if recent else 0
    avg_earlier = round(sum(earlier) / len(earlier)) if earlier else 0
    return {"article": title, "avg_monthly_views_last_12m": avg_recent,
            "avg_monthly_views_prior_12m": avg_earlier,
            "trend_pct": _pct_change(avg_earlier, avg_recent),
            "monthly_views": views[-12:],
            "url": f"https://{lang}.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}"}


# --- GitHub (existing open-source competition for software ideas) -----------

class GitHubInput(BaseModel):
    query: str = Field(description="Keywords describing the software, e.g. 'invoice generator'.")


def github_projects(args: GitHubInput, http: httpx.Client) -> dict:
    """How many open-source projects already do this, and the most popular ones."""
    r = http.get("https://api.github.com/search/repositories",
                 params={"q": args.query, "sort": "stars", "order": "desc", "per_page": 5},
                 headers={"Accept": "application/vnd.github+json"})
    r.raise_for_status()
    data = r.json()
    return {"total_repositories": data["total_count"],
            "top_repositories": [{"name": i["full_name"], "stars": i["stargazers_count"],
                                  "description": i.get("description"),
                                  "last_push": (i.get("pushed_at") or "")[:10], "url": i["html_url"]}
                                 for i in data["items"]]}


# --- World Bank (country-level market size and purchasing power) ------------

class CountryInput(BaseModel):
    country_code: str = Field(description="ISO country code, e.g. 'US', 'PT', 'TR', 'DE'.")


WORLD_BANK_INDICATORS = {
    "population": "SP.POP.TOTL",
    "gdp_per_capita_usd": "NY.GDP.PCAP.CD",
    "inflation_pct": "FP.CPI.TOTL.ZG",
    "internet_users_pct": "IT.NET.USER.ZS",
    "urban_population_pct": "SP.URB.TOTL.IN.ZS",
}


def country_economics(args: CountryInput, http: httpx.Client) -> dict:
    """Population, purchasing power, inflation and internet reach for a country."""
    code = args.country_code.strip().upper()
    out: dict[str, Any] = {"country_code": code}
    for label, indicator in WORLD_BANK_INDICATORS.items():
        r = http.get(f"https://api.worldbank.org/v2/country/{quote(code)}/indicator/{indicator}",
                     params={"format": "json", "mrnev": 1})
        r.raise_for_status()
        payload = r.json()
        rows = payload[1] if isinstance(payload, list) and len(payload) > 1 and payload[1] else []
        if rows:
            out["country"] = rows[0]["country"]["value"]
            value = rows[0]["value"]
            out[label] = {"value": round(value, 2) if isinstance(value, float) else value,
                          "year": rows[0]["date"]}
    if "country" not in out:
        return {"error": f"No World Bank data for country code '{code}'."}
    return out


# --- Unit economics calculator (deterministic, no network) ------------------

class UnitEconomicsInput(BaseModel):
    price: float = Field(description="Price the customer pays per unit or per month.")
    unit_cost: float = Field(description="Variable cost per unit or per customer-month (materials, shipping, fees).")
    monthly_fixed_costs: float = Field(default=0, description="Rent, salaries, software, etc. per month.")
    customer_acquisition_cost: float = Field(default=0, description="Marketing spend to win one customer.")
    purchases_per_customer: float = Field(
        default=1, description="Expected purchases (or months subscribed) per customer over their lifetime.")
    startup_costs: float = Field(default=0, description="One-time investment before launch.")


def unit_economics(args: UnitEconomicsInput, http: httpx.Client | None = None) -> dict:
    """Margins, break-even volume, customer lifetime value and payback."""
    margin = args.price - args.unit_cost
    out: dict[str, Any] = {
        "gross_margin_per_unit": round(margin, 2),
        "gross_margin_pct": round(100 * margin / args.price, 1) if args.price else None,
    }
    if margin <= 0:
        out["warning"] = "Each sale loses money before fixed costs. The price must rise or costs must fall."
        return out
    ltv = margin * args.purchases_per_customer
    out["lifetime_gross_profit_per_customer"] = round(ltv, 2)
    out["break_even_units_per_month"] = round(args.monthly_fixed_costs / margin, 1)
    if args.customer_acquisition_cost:
        out["ltv_to_cac_ratio"] = round(ltv / args.customer_acquisition_cost, 2)
        out["ltv_to_cac_note"] = "3 or more is healthy; under 1 means each customer loses money."
        out["units_to_recover_cac"] = round(args.customer_acquisition_cost / margin, 1)
    if args.startup_costs:
        out["units_to_recover_startup_costs"] = round(args.startup_costs / margin)
    return out


# --- Registry ----------------------------------------------------------------

@dataclass(frozen=True)
class DataTool:
    name: str
    description: str
    input_model: type[BaseModel]
    run: Callable[[Any, httpx.Client], dict]
    status: str  # shown to the user while the tool runs

    def definition(self) -> dict:
        schema = self.input_model.model_json_schema()
        schema.pop("title", None)
        return {"name": self.name, "description": self.description, "input_schema": schema,
                "eager_input_streaming": True}


DATA_TOOLS: dict[str, DataTool] = {t.name: t for t in [
    DataTool("hacker_news", "Count and list Hacker News stories about a topic over the past two years. "
             "Good for software, startups and tech products.", HackerNewsInput, hacker_news,
             "Checking Hacker News"),
    DataTool("reddit", "Search Reddit posts from the past year to see which communities discuss a problem "
             "or product and how engaged they are. Good for consumer demand and pain points.",
             RedditInput, reddit, "Reading Reddit discussions"),
    DataTool("wikipedia_interest", "Monthly Wikipedia page views for a topic over 24 months, a stable proxy "
             "for public interest and whether it is growing or shrinking.", WikipediaInput,
             wikipedia_interest, "Measuring public interest"),
    DataTool("github_projects", "Count existing open-source projects for a software idea and list the most "
             "popular ones. Use for software tools and apps.", GitHubInput, github_projects,
             "Scanning GitHub for existing tools"),
    DataTool("country_economics", "World Bank data for a country: population, GDP per capita, inflation, "
             "internet and urban population share. Use when the idea targets a specific country.",
             CountryInput, country_economics, "Pulling World Bank economic data"),
    DataTool("unit_economics", "Calculate margins, break-even volume, lifetime value, LTV:CAC and payback from "
             "price and cost assumptions. Use whenever you discuss pricing or profitability.",
             UnitEconomicsInput, unit_economics, "Running the numbers"),
]}


def run_data_tool(name: str, raw_input: Any, http: httpx.Client | None = None) -> tuple[dict, bool]:
    """Validate input and run a tool. Returns (result, is_error)."""
    tool = DATA_TOOLS[name]
    try:
        args = tool.input_model.model_validate(raw_input)
    except Exception as e:  # pydantic.ValidationError, or a non-dict input
        return {"error": f"Invalid input: {e}"}, True
    own_client = http is None
    http = http or _client()
    try:
        return tool.run(args, http), False
    except httpx.HTTPStatusError as e:
        return {"error": f"{name} source returned HTTP {e.response.status_code}. Use other sources."}, True
    except (httpx.HTTPError, KeyError, ValueError, TypeError) as e:
        return {"error": f"{name} source unavailable ({type(e).__name__}). Use other sources."}, True
    finally:
        if own_client:
            http.close()


def _pct_change(before: float, after: float) -> float | None:
    if not before:
        return None
    return round(100 * (after - before) / before, 1)
