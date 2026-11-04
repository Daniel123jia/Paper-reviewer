"""Conservative P2-B applicability and domain configuration."""

from dataclasses import dataclass


@dataclass(frozen=True)
class DimensionRule:
    id: str
    name: str
    prompt_name: str
    focus: tuple[str, ...]


DIMENSIONS = (
    DimensionRule("D1", "Novelty and core contribution", "novelty-contribution",
                  ("claim scope", "mechanism distinction", "contribution evidence")),
    DimensionRule("D2", "Theory, algorithm and method", "theory-method",
                  ("assumptions", "algorithm mechanism", "formula limitations")),
    DimensionRule("D3", "Experimental design and results", "experiment-results",
                  ("comparison fairness", "ablation", "result interpretation")),
    DimensionRule("D4", "Argument and conclusion boundaries", "argument-consistency",
                  ("claim consistency", "source conflicts", "conclusion scope")),
    DimensionRule("D5", "Technical expression and reproducibility", "technical-reproducibility",
                  ("implementation details", "training protocol", "reproducibility")),
    DimensionRule("D6", "Related work and academic positioning", "literature-positioning",
                  ("research gap", "internal citations", "external-check leads")),
)


def configuration(plan):
    text = " ".join((plan.paper_type, plan.research_domain,
                     plan.methodological_paradigm)).casefold()
    if any(term in text for term in ("ai algorithm", "machine learning", "deep learning",
                                      "natural language processing", "computer vision")):
        return "ai-algorithm/v1", []
    return "generic-academic/v1", ["domain_specific_rules_unavailable"]


def applicability(rule, plan):
    excluded = {item.casefold() for item in plan.excluded_dimensions}
    if rule.id.casefold() in excluded or rule.name.casefold() in excluded:
        return "not_applicable", "ReviewPlan explicitly excludes this dimension"
    paradigm = plan.methodological_paradigm.casefold()
    if rule.id == "D3" and any(term in paradigm for term in ("pure theory", "theoretical only",
                                                               "non-empirical")):
        return "not_applicable", "No empirical experiment is claimed by this paper type"
    if rule.id == "D3" and plan.missing_materials:
        return "partially_applicable", "Required experimental material is incomplete"
    return "applicable", "Relevant to the paper type and claimed research"
