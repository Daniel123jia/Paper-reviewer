from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LineageAuditReport:
    passed: bool
    errors: list[str]
    checks: dict[str, bool]


def _walk(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _source_ids(bundle: dict) -> set[str]:
    ids: set[str] = set()
    for item in _walk(bundle):
        for key in ("id", "evidence_id", "reading_unit_id", "object_id", "source_ref_id",
                    "source_id"):
            value = item.get(key)
            if isinstance(value, str) and value:
                ids.add(value)
        for key in ("evidence_ids", "original_evidence_ids"):
            values = item.get(key)
            if isinstance(values, list):
                ids.update(value for value in values if isinstance(value, str))
    return ids


def _referenced_source_ids(payload: dict) -> set[str]:
    refs: set[str] = set()
    for item in _walk(payload):
        for key, value in item.items():
            if key in {"source_ref_id", "source_id", "evidence_id", "reading_unit_id"} and \
                    isinstance(value, str):
                refs.add(value)
            elif key in {"source_refs", "evidence_refs", "counterevidence_refs",
                         "source_ref_ids", "reading_unit_ids", "evidence_ids"} and \
                    isinstance(value, list):
                for entry in value:
                    if isinstance(entry, str):
                        refs.add(entry)
                    elif isinstance(entry, dict):
                        candidate = (entry.get("source_ref_id") or entry.get("source_id") or
                                     entry.get("evidence_id") or
                                     entry.get("reading_unit_id"))
                        if isinstance(candidate, str):
                            refs.add(candidate)
    return refs


def audit_lineage(artifacts: dict[str, dict]) -> LineageAuditReport:
    errors: list[str] = []
    checks: dict[str, bool] = {}
    p1 = artifacts.get("p1") or {}
    document = p1.get("document") or {}
    bundle = p1.get("source_bundle") or {}
    bundle_document = bundle.get("document") or {}
    expected_document = document.get("id") or bundle_document.get("id")
    expected_sha = document.get("sha256") or bundle_document.get("sha256")
    expected_bundle = bundle.get("bundle_id")

    checks["p1_document_snapshot"] = bool(expected_document and expected_sha and expected_bundle)
    if not checks["p1_document_snapshot"]:
        errors.append("P1 artifact is missing document, PDF hash, or source_bundle_id")
    if document.get("id") and bundle_document.get("id") != document.get("id"):
        errors.append("P1 source bundle document_id differs from uploaded document_id")
    if document.get("sha256") and bundle_document.get("sha256") and \
            bundle_document.get("sha256") != document.get("sha256"):
        errors.append("P1 source bundle PDF SHA-256 differs from uploaded document")

    identity_keys = {
        "document_id": expected_document,
        "source_bundle_id": expected_bundle,
    }
    for stage, payload in artifacts.items():
        if stage == "p1" or not isinstance(payload, dict):
            continue
        for key, expected in identity_keys.items():
            actual = payload.get(key)
            if actual is not None and expected is not None and actual != expected:
                errors.append(f"{stage} {key}={actual} differs from frozen {expected}")
        result = payload.get("result")
        if isinstance(result, dict):
            for key, expected in identity_keys.items():
                actual = result.get(key)
                if actual is not None and expected is not None and actual != expected:
                    errors.append(f"{stage} result {key}={actual} differs from frozen {expected}")

    canonical = _source_ids(bundle)
    checks["source_index_nonempty"] = bool(canonical)
    for stage, payload in artifacts.items():
        if stage == "p1" or not isinstance(payload, dict):
            continue
        for ref in sorted(_referenced_source_ids(payload) - canonical):
            errors.append(f"{stage} references unknown Source Bundle object {ref}")

    # P2-C must retain every original candidate identifier in its inventory or lineage.
    p2b = artifacts.get("p2b") or {}
    p2c = artifacts.get("p2c") or {}
    candidates = p2b.get("candidates") or []
    candidate_ids = {item.get("issue_id") for item in candidates if isinstance(item, dict)}
    p2c_ids: set[str] = set()
    for item in _walk(p2c):
        for key in ("original_issue_id", "source_candidate_ids", "original_candidate_ids"):
            value = item.get(key)
            if isinstance(value, str):
                p2c_ids.add(value)
            elif isinstance(value, list):
                p2c_ids.update(entry for entry in value if isinstance(entry, str))
    missing_candidates = candidate_ids - p2c_ids
    if missing_candidates:
        errors.append("P2-C lineage omits original candidates: " + ", ".join(sorted(missing_candidates)))
    checks["p2c_candidate_lineage"] = not missing_candidates

    # Every P2-C verification task needs a P3 task result when P3 claims completion/limitation.
    p3 = artifacts.get("p3") or {}
    required_tasks = {item.get("verification_task_id") for item in _walk(p2c)
                      if isinstance(item.get("verification_task_id"), str)}
    adjudicated_tasks = {item.get("verification_task_id") for item in _walk(p3)
                         if isinstance(item.get("verification_task_id"), str)}
    missing_tasks = required_tasks - adjudicated_tasks
    if p3.get("status") in {"completed", "limited"} and missing_tasks:
        errors.append("P3 omits verification tasks: " + ", ".join(sorted(missing_tasks)))
    checks["p3_task_disposition"] = not missing_tasks

    return LineageAuditReport(passed=not errors, errors=errors, checks=checks)
