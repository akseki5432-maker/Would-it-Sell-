"""Data models for a Would-it-Sell assessment."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# How much each dimension contributes to the overall score. Market demand and
# monetization dominate: a well-built thing nobody pays for still fails.
DIMENSION_WEIGHTS: dict[str, float] = {
    "market_demand": 0.30,
    "monetization": 0.25,
    "competition": 0.15,
    "feasibility": 0.15,
    "timing": 0.15,
}

DimensionName = Literal["market_demand", "competition", "monetization", "feasibility", "timing"]


class DimensionScore(BaseModel):
    name: DimensionName
    score: int = Field(description="0 (very bad) to 10 (excellent). For competition, 10 means an open market.")
    rationale: str = Field(description="One or two sentences citing the evidence behind the score.")


class Competitor(BaseModel):
    name: str
    note: str = Field(description="What they sell, their price point, and how strong they look.")


class Source(BaseModel):
    title: str
    url: str


class Assessment(BaseModel):
    """What the model returns. Overall score and verdict are computed in code."""

    idea_summary: str
    category: Literal["business", "physical_product", "software_tool", "service", "other"]
    dimensions: list[DimensionScore]
    target_customers: list[str]
    competitors: list[Competitor]
    pricing_benchmark: str = Field(description="What customers currently pay for comparable offerings.")
    revenue_model: str = Field(description="The most realistic way this makes money.")
    key_risks: list[str]
    validation_steps: list[str] = Field(description="Cheap experiments to run before investing serious money.")
    confidence: Literal["low", "medium", "high"] = Field(description="How strong the evidence found was.")
    sources: list[Source]


Verdict = Literal["strong_go", "promising", "risky", "unlikely"]


class Report(BaseModel):
    idea: str
    overall_score: int
    verdict: Verdict
    assessment: Assessment


def overall_score(dimensions: list[DimensionScore]) -> int:
    """Weighted 0-100 score. Missing dimensions count as 0, duplicates use the last value."""
    by_name = {d.name: max(0, min(10, d.score)) for d in dimensions}
    total = sum(weight * by_name.get(name, 0) for name, weight in DIMENSION_WEIGHTS.items())
    return round(total * 10)


def verdict_for(score: int) -> Verdict:
    if score >= 75:
        return "strong_go"
    if score >= 60:
        return "promising"
    if score >= 40:
        return "risky"
    return "unlikely"
