"""Would it Sell? Estimate whether an idea can make money, using live market research."""

from .analyzer import AnalysisError, analyze
from .models import Assessment, Report

__all__ = ["AnalysisError", "Assessment", "Report", "analyze"]
