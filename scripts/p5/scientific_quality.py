from __future__ import annotations

from collections import Counter
from typing import Iterable, Literal, TypedDict


JudgmentClass = Literal["A", "B", "C", "D", "E"]


class QualityMetrics(TypedDict):
    counts: dict[str, int]
    effective_issue_numerator: int
    effective_issue_denominator: int
    effective_issue_ratio: float | None
    explicit_false_positive_count: int
    unresolved_count: int
    low_value_count: int


def compute_quality_metrics(judgments: Iterable[JudgmentClass]) -> QualityMetrics:
    """Compute small-sample issue metrics without treating unresolved items as errors.

    A/B are valuable findings, C is an explicit false positive, D is unresolved and is
    excluded from the correctness denominator, and E is judged but low-value. Callers
    must report the numerator, denominator, and unresolved count alongside the ratio.
    """
    counts = Counter(judgments)
    normalized = {key: counts[key] for key in ("A", "B", "C", "D", "E")}
    numerator = normalized["A"] + normalized["B"]
    denominator = numerator + normalized["C"] + normalized["E"]
    return {
        "counts": normalized,
        "effective_issue_numerator": numerator,
        "effective_issue_denominator": denominator,
        "effective_issue_ratio": numerator / denominator if denominator else None,
        "explicit_false_positive_count": normalized["C"],
        "unresolved_count": normalized["D"],
        "low_value_count": normalized["E"],
    }
