"""Narrow deterministic checks, with explicit transcription and scope limits."""

import hashlib
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

from pydantic import Field, field_validator

from backend.mathcheck import source_number_matches, verify_equation_pair
from backend.p2a_schema import StrictModel
from backend.p3_schema import ToolCheck


PROMPT_VERSION = "technical-evidence-assessment/v1"
PROMPT = (Path(__file__).with_name("prompts") / "p3" /
          "technical-evidence-assessment.v1.txt")


class MetricRecord(StrictModel):
    record_id: str
    source_id: str
    dataset: str
    metric: str
    model_name: str
    configuration: str
    value: str
    unit: str

    @field_validator("value", mode="before")
    @classmethod
    def normalize_json_number(cls, value):
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise ValueError("metric value must be a decimal literal")
        return str(value)


class ScalarFormulaProposal(StrictModel):
    check_id: str
    source_id: str
    lhs: str
    rhs: str
    assumptions: dict[str, str]
    domain_note: str


class TechnicalEvidenceProposal(StrictModel):
    metric_records: list[MetricRecord] = Field(max_length=16)
    scalar_formula_checks: list[ScalarFormulaProposal] = Field(max_length=4)
    unavailable_checks: list[str]
    evidence_level: str = Field(default="model_proposal", pattern="^model_proposal$")


def validate_technical_proposal(raw, pack):
    proposal = TechnicalEvidenceProposal.model_validate(raw)
    allowed = set(pack.search.selected_source_ids)
    if any(item.source_id not in allowed for item in
           proposal.metric_records + proposal.scalar_formula_checks):
        raise ValueError("technical proposal cites a source outside its context pack")
    ids = [item.record_id for item in proposal.metric_records] + [item.check_id for item in
          proposal.scalar_formula_checks]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate technical proposal ID")
    for item in proposal.metric_records:
        try:
            Decimal(item.value)
        except InvalidOperation as exc:
            raise ValueError("metric value must be a finite decimal") from exc
        if not Decimal(item.value).is_finite():
            raise ValueError("metric value must be a finite decimal")
    return proposal


def _identifier(task_id, kind, parts):
    return f"tool_{hashlib.sha256((task_id + '|' + kind + '|' + '|'.join(parts)).encode()).hexdigest()[:20]}"


def _literal_present(number, text):
    return bool(re.search(r"(?<![\w.])" + re.escape(number) + r"(?![\w.])", text))


def _same_setting(left, right):
    return all(getattr(left, key).strip().casefold() == getattr(right, key).strip().casefold()
               for key in ("dataset", "metric", "model_name", "configuration", "unit"))


def _sample_fits_assumptions(sample, assumptions):
    for name, value in sample.items():
        kind = assumptions.get(name, "real")
        if kind == "positive" and value <= 0:
            return False
        if kind == "nonzero" and value == 0:
            return False
        if kind == "integer" and value != int(value):
            return False
    return True


def run_technical_checks(task, proposal, sources, observations):
    """Calculate only on cited literals; never promote PDF extraction to visual proof."""
    source_index = {item.source_id: item for item in sources}
    observed = {ref_id: item for item in observations
                for ref_id in item.related_source_ref_ids}
    checks = []
    for source_id in dict.fromkeys(ref.source_id for ref in
                                   task.source_refs_to_check + task.counterevidence_refs_to_check):
        source = source_index.get(source_id)
        if source and source.source_type in {"table", "formula", "figure"}:
            checks.append(ToolCheck(tool_result_id=_identifier(task.verification_task_id,
                "pdf_region", [source_id]), verification_task_id=task.verification_task_id,
                check_type="pdf_region", method="PyMuPDF PDF region extraction",
                input_refs=[source_id], input_values={},
                result={"region_image_sha256": source.region_image_sha256,
                        "source_status": source.source_verification_status},
                status="inconclusive" if source.region_image_sha256 else "blocked",
                limitations=["Generated region is not an independent visual confirmation"]))
    trusted_literals = {}
    for record in proposal.metric_records:
        source = source_index[record.source_id]
        actual = observed.get(record.source_id)
        frozen_match = _literal_present(record.value, source.relevant_excerpt)
        pdf_match = bool(actual and _literal_present(record.value, actual.observed_statement))
        trusted_literals[record.record_id] = frozen_match and pdf_match
        checks.append(ToolCheck(tool_result_id=_identifier(task.verification_task_id,
            "metric", [record.record_id]), verification_task_id=task.verification_task_id,
            check_type="table" if source.source_type == "table" else "numeric",
            method="literal metric value in frozen extraction and independent PDF text pass",
            input_refs=[record.source_id], input_values={"value": record.value,
                "dataset": record.dataset, "metric": record.metric,
                "model": record.model_name, "configuration": record.configuration,
                "unit": record.unit},
            result={"frozen_literal_present": frozen_match, "pdf_text_literal_present": pdf_match,
                    "source_status": source.source_verification_status},
            status="completed" if frozen_match and pdf_match and
                source.source_verification_status == "pdf_text_located" else "inconclusive",
            limitations=["Literal presence does not verify table row/column association or experiment setup"]
                        if source.source_type == "table" else
                        ["Literal presence does not verify metric semantics or experiment setup"]))
    records = proposal.metric_records
    for index, left in enumerate(records):
        for right in records[index + 1:]:
            same = _same_setting(left, right)
            verified_inputs = trusted_literals[left.record_id] and trusted_literals[right.record_id]
            difference = str(Decimal(left.value) - Decimal(right.value)) if same and verified_inputs else None
            checks.append(ToolCheck(tool_result_id=_identifier(task.verification_task_id,
                "pair", [left.record_id, right.record_id]),
                verification_task_id=task.verification_task_id, check_type="numeric",
                method="Decimal comparison after exact dataset/metric/model/configuration/unit match",
                input_refs=[left.source_id, right.source_id],
                input_values={"left": left.value, "right": right.value},
                result={"same_reported_setting": same, "literal_inputs_located": verified_inputs,
                    "difference": difference, "comparison":
                    "not_comparable" if not same else "source_unverified" if not verified_inputs else
                    "same_value" if Decimal(left.value) == Decimal(right.value) else
                    "potential_discrepancy"},
                status="completed" if verified_inputs and all(
                    source_index[source_id].source_verification_status == "pdf_text_located"
                    for source_id in (left.source_id, right.source_id)) else "inconclusive",
                limitations=["Configuration equality is based on the recorded labels; original PDF table structure and experimental meaning may need review"] ))
    for formula in proposal.scalar_formula_checks:
        source = source_index[formula.source_id]
        actual = observed.get(formula.source_id)
        try:
            result = verify_equation_pair(formula.lhs, formula.rhs, formula.assumptions)
            literal_check = source_number_matches(formula.lhs, formula.rhs,
                source.relevant_excerpt, actual.observed_statement if actual else "")
            sample = (result.get("counterexample") or {}).get("symbols", {})
            result["sample_satisfies_declared_assumptions"] = (
                _sample_fits_assumptions(sample, formula.assumptions) if sample else None)
            result["literal_source_check"] = literal_check
            result["domain_note"] = formula.domain_note
        except (ValueError, SyntaxError, TypeError) as exc:
            result = {"status": "unsupported_expression", "error_class": type(exc).__name__,
                      "domain_note": formula.domain_note}
        checks.append(ToolCheck(tool_result_id=_identifier(task.verification_task_id,
            "formula", [formula.check_id]), verification_task_id=task.verification_task_id,
            check_type="scalar_formula", method="bounded SymPy scalar comparison",
            input_refs=[formula.source_id], input_values={"lhs": formula.lhs, "rhs": formula.rhs},
            result=result, status="inconclusive",
            limitations=["Conditional scalar computation; PDF transcription, tensor meaning and full theoretical assumptions are not independently verified"] +
                (["Formula source requires visual review"] if source.source_verification_status != "human_confirmed" else [])))
    if task.external_verification_required:
        checks.append(ToolCheck(tool_result_id=_identifier(task.verification_task_id,
            "external", [task.consolidated_issue_id]),
            verification_task_id=task.verification_task_id, check_type="external",
            method="explicit-source availability check", input_refs=[], input_values={},
            result={"status": "external_check_unavailable"}, status="blocked",
            limitations=["No independently accessible external literature, code or data was supplied"] ))
    return checks
