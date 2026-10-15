import time

import httpx

from would_it_sell.sources import DATA_TOOLS, run_data_tool


def mock_http(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_tool_definitions_are_valid():
    for tool in DATA_TOOLS.values():
        d = tool.definition()
        assert d["input_schema"]["type"] == "object"
        assert d["name"] == tool.name and d["description"]


def test_invalid_input_is_reported_not_raised():
    result, is_error = run_data_tool("reddit", {"wrong": 1}, mock_http(lambda r: httpx.Response(500)))
    assert is_error and "Invalid input" in result["error"]


def test_http_failure_becomes_error_result():
    result, is_error = run_data_tool("reddit", {"query": "x"}, mock_http(lambda r: httpx.Response(429)))
    assert is_error and "429" in result["error"]


def test_hacker_news_trend():
    def handler(req):
        p = req.url.params
        if p.get("hitsPerPage") == "0":
            start = int(p["numericFilters"].split(",")[0].split(">")[1])
            return httpx.Response(200, json={"nbHits": 30 if _is_recent(start) else 20})
        return httpx.Response(200, json={"hits": [
            {"objectID": "1", "title": "Show HN: Meal kits", "points": 120, "num_comments": 40,
             "created_at": "2026-03-01T00:00:00Z"}]})

    result, is_error = run_data_tool("hacker_news", {"query": "meal kits"}, mock_http(handler))
    assert not is_error
    assert result["stories_last_12_months"] == 30 and result["stories_prior_12_months"] == 20
    assert result["trend_pct"] == 50.0
    assert result["top_stories"][0]["url"] == "https://news.ycombinator.com/item?id=1"


def _is_recent(start):
    return start > time.time() - 400 * 24 * 3600


def test_reddit_aggregates_subreddits():
    posts = [{"data": {"title": f"t{i}", "subreddit": "mealprep" if i < 3 else "fitness", "score": i,
                       "num_comments": 1, "permalink": f"/r/x/{i}"}} for i in range(5)]
    result, _ = run_data_tool("reddit", {"query": "q"},
                              mock_http(lambda r: httpx.Response(200, json={"data": {"children": posts}})))
    assert result["posts_found_past_year"] == 5
    assert result["top_subreddits"] == ["mealprep", "fitness"]
    assert result["top_posts"][0]["upvotes"] == 4


def test_wikipedia_interest_trend():
    def handler(req):
        if "opensearch" in str(req.url):
            return httpx.Response(200, json=["meal kit", ["Meal kit"], [], []])
        assert "/Meal_kit/monthly/" in req.url.path
        return httpx.Response(200, json={"items": [{"views": 100}] * 12 + [{"views": 150}] * 12})

    result, is_error = run_data_tool("wikipedia_interest", {"topic": "meal kit"}, mock_http(handler))
    assert not is_error
    assert result["avg_monthly_views_last_12m"] == 150 and result["trend_pct"] == 50.0


def test_wikipedia_missing_article():
    result, _ = run_data_tool("wikipedia_interest", {"topic": "zzz"},
                              mock_http(lambda r: httpx.Response(200, json=["zzz", [], [], []])))
    assert "No Wikipedia article" in result["error"]


def test_github_projects():
    body = {"total_count": 42, "items": [{"full_name": "a/b", "stargazers_count": 900, "description": "d",
                                          "pushed_at": "2026-09-01T00:00:00Z", "html_url": "https://github.com/a/b"}]}
    result, _ = run_data_tool("github_projects", {"query": "invoice"},
                              mock_http(lambda r: httpx.Response(200, json=body)))
    assert result["total_repositories"] == 42 and result["top_repositories"][0]["stars"] == 900


def test_country_economics():
    def handler(req):
        return httpx.Response(200, json=[{}, [{"country": {"value": "Portugal"}, "value": 25000.123,
                                               "date": "2025"}]])
    result, _ = run_data_tool("country_economics", {"country_code": "pt"}, mock_http(handler))
    assert result["country"] == "Portugal" and result["country_code"] == "PT"
    assert result["gdp_per_capita_usd"] == {"value": 25000.12, "year": "2025"}


def test_unit_economics():
    result, is_error = run_data_tool("unit_economics", {
        "price": 12, "unit_cost": 7, "monthly_fixed_costs": 3000, "customer_acquisition_cost": 40,
        "purchases_per_customer": 30, "startup_costs": 15000})
    assert not is_error
    assert result["gross_margin_per_unit"] == 5 and result["gross_margin_pct"] == 41.7
    assert result["break_even_units_per_month"] == 600
    assert result["ltv_to_cac_ratio"] == 3.75
    assert result["units_to_recover_startup_costs"] == 3000


def test_unit_economics_negative_margin():
    result, _ = run_data_tool("unit_economics", {"price": 5, "unit_cost": 8})
    assert "warning" in result
