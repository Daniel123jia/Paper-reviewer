"""Versioned N3 partition and checkpoint contracts for P2-A continuation runs."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Callable

from backend.parse_quality import assess_input_readiness
from backend.p2a_engine import (
    CallTrace,
    PROMPTS,
    RunBudget,
    VERSIONS,
    _invoke,
    _merge_claim_maps,
    _object_catalog,
    _profile_context_for_part,
    _source_text,
    chunk_reading_units,
)
from backend.p2a_schema import ClaimEvidenceMap, validate_source_references


N3_PROMPT_VERSION = VERSIONS["contribution-evidence-mapper"]
N3_SCHEMA_VERSION = "1.1"
CHECKPOINT_SCHEMA_VERSION = "1.0"
SUPPORTED_RESULT_SCHEMA_VERSIONS = {"1.0", "1.1"}


@dataclass(frozen=True)
class N3PartitionDescriptor:
    partition_id: str
    partition_index: int
    partition_count: int
    unit_ids: tuple[str, ...]
    input_fingerprint: str
    user_text: str


@dataclass
class StoredN3Checkpoint:
    checkpoint_id: str
    generating_run_id: str
    origin_checkpoint_id: str | None
    origin_run_id: str | None
    generation_mode: str
    partition_id: str
    partition_index: int
    partition_count: int
    unit_ids: tuple[str, ...]
    input_fingerprint: str
    prompt_version: str
    schema_version: str
    result_hash: str
    result: ClaimEvidenceMap


def _canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _profile_fingerprint_payload(profile) -> dict:
    """Exclude only run-local identity; keep all scientific/source metadata."""
    return profile.model_dump(exclude={"review_run_id"}, mode="json")


def _document_sha(bundle: dict) -> str:
    return str(bundle.get("document", {}).get("sha256") or "")


def checkpoint_result_hash(result) -> str:
    payload = result.model_dump(mode="json")
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def validate_inherited_understanding_assets(bundle: dict, run_id: str, plan, profile) -> None:
    """Validate the complete N1/N2 identity used by an N3-only continuation."""
    expected_document_id = bundle["document"]["id"]
    expected_bundle_id = bundle["bundle_id"]
    for result, prompt_key in (
        (plan, "paper-type-planner"),
        (profile, "paper-understanding"),
    ):
        if (
            result.document_id != expected_document_id
            or result.source_bundle_id != expected_bundle_id
            or result.review_run_id != run_id
            or result.prompt_version != VERSIONS[prompt_key]
            or result.schema_version not in SUPPORTED_RESULT_SCHEMA_VERSIONS
        ):
            raise ValueError("inherited understanding result metadata mismatch")
    if plan.schema_version != profile.schema_version:
        raise ValueError("inherited understanding schema versions disagree")
    validate_source_references(profile, bundle)


def checkpoint_content_rebinds_exactly(
    origin: StoredN3Checkpoint, descendant: StoredN3Checkpoint
) -> bool:
    rebound = origin.result.model_copy(
        update={"review_run_id": descendant.generating_run_id}, deep=True
    )
    return rebound.model_dump(mode="json") == descendant.result.model_dump(mode="json")


def build_n3_partition_descriptors(
    bundle: dict,
    profile,
    target_journal: str | None,
    special_requirements: str | None,
    *,
    max_chars: int = 9000,
) -> list[N3PartitionDescriptor]:
    """Build exact, run-id-independent N3 inputs and reuse fingerprints."""
    units = bundle.get("reading_units") or []
    if not units:
        return []
    quality = assess_input_readiness(bundle, "section_summary")
    context = (
        f'Document {bundle["document"]["id"]}; Source Bundle {bundle["bundle_id"]}.\n'
        f"Quality: {json.dumps(quality, ensure_ascii=False)}\n"
        f'Journal: {target_journal or "not supplied"}\n'
        f'User requirements: {special_requirements or "not supplied"}\n'
        f'Sections: {json.dumps([item["title"] for item in bundle["sections"]], ensure_ascii=False)}\n'
        f"Technical object status:\n{_object_catalog(bundle)}\n"
    )
    prompt_file_version = VERSIONS["contribution-evidence-mapper"].split("/")[-1]
    system = (PROMPTS / f"contribution-evidence-mapper.{prompt_file_version}.txt").read_text(
        encoding="utf-8"
    )
    chunks = chunk_reading_units(units, max_chars)
    profile_payload = _profile_fingerprint_payload(profile)
    descriptors = []
    for index, part in enumerate(chunks):
        label = f"{index + 1}/{len(chunks)}"
        user_text = (
            context
            + f"\nPart {label}. Map claims from these reading units; "
            "the PaperProfile facts below are projected by their source IDs.\n"
            + _profile_context_for_part(profile, part)
            + "\nReading units:\n"
            + _source_text(part)
        )
        unit_ids = tuple(item["id"] for item in part)
        fingerprint_payload = {
            "checkpoint_schema_version": CHECKPOINT_SCHEMA_VERSION,
            "document_id": bundle["document"]["id"],
            "source_bundle_id": bundle["bundle_id"],
            "source_snapshot_id": bundle["document"].get("snapshot_id"),
            "pdf_sha256": _document_sha(bundle),
            "prompt_version": N3_PROMPT_VERSION,
            "result_schema_version": N3_SCHEMA_VERSION,
            "source_profile_schema_version": profile.schema_version,
            "target_journal": target_journal,
            "special_requirements": special_requirements,
            "profile": profile_payload,
            "unit_ids": unit_ids,
            "system": system,
            "user": user_text,
        }
        input_fingerprint = hashlib.sha256(
            _canonical_json(fingerprint_payload).encode("utf-8")
        ).hexdigest()
        stable_partition = {
            "document_id": bundle["document"]["id"],
            "source_bundle_id": bundle["bundle_id"],
            "unit_ids": unit_ids,
            "input_fingerprint": input_fingerprint,
        }
        partition_id = "n3part_" + hashlib.sha256(
            _canonical_json(stable_partition).encode("utf-8")
        ).hexdigest()[:20]
        descriptors.append(
            N3PartitionDescriptor(
                partition_id=partition_id,
                partition_index=index,
                partition_count=len(chunks),
                unit_ids=unit_ids,
                input_fingerprint=input_fingerprint,
                user_text=user_text,
            )
        )
    return descriptors


def _checkpoint_id(run_id: str, descriptor: N3PartitionDescriptor, result_hash: str) -> str:
    payload = f"{run_id}|{descriptor.partition_id}|{descriptor.input_fingerprint}|{result_hash}"
    return "n3cp_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _stored_checkpoint(
    run_id: str,
    descriptor: N3PartitionDescriptor,
    result: ClaimEvidenceMap,
    *,
    generation_mode: str,
    origin: StoredN3Checkpoint | None = None,
) -> StoredN3Checkpoint:
    result_hash = checkpoint_result_hash(result)
    return StoredN3Checkpoint(
        checkpoint_id=_checkpoint_id(run_id, descriptor, result_hash),
        generating_run_id=run_id,
        origin_checkpoint_id=origin.checkpoint_id if origin else None,
        origin_run_id=origin.generating_run_id if origin else None,
        generation_mode=generation_mode,
        partition_id=descriptor.partition_id,
        partition_index=descriptor.partition_index,
        partition_count=descriptor.partition_count,
        unit_ids=descriptor.unit_ids,
        input_fingerprint=descriptor.input_fingerprint,
        prompt_version=N3_PROMPT_VERSION,
        schema_version=N3_SCHEMA_VERSION,
        result_hash=result_hash,
        result=result,
    )


def inherit_n3_checkpoint(
    checkpoint: StoredN3Checkpoint,
    descriptor: N3PartitionDescriptor,
    bundle: dict,
    run_id: str,
) -> StoredN3Checkpoint:
    result = validate_checkpoint(checkpoint, descriptor, bundle, run_id)
    return _stored_checkpoint(
        run_id, descriptor, result, generation_mode="inherited", origin=checkpoint
    )


def validate_checkpoint(
    checkpoint: StoredN3Checkpoint,
    descriptor: N3PartitionDescriptor,
    bundle: dict,
    run_id: str,
) -> ClaimEvidenceMap:
    if not isinstance(checkpoint, StoredN3Checkpoint):
        raise ValueError("checkpoint has unsupported representation")
    if checkpoint.generation_mode not in {"generated", "inherited"}:
        raise ValueError("checkpoint generation mode mismatch")
    if checkpoint.generation_mode == "generated" and (
        checkpoint.origin_checkpoint_id is not None or checkpoint.origin_run_id is not None
    ):
        raise ValueError("checkpoint generated origin mismatch")
    if checkpoint.generation_mode == "inherited" and (
        not checkpoint.origin_checkpoint_id or not checkpoint.origin_run_id
    ):
        raise ValueError("checkpoint inherited origin missing")
    if (
        checkpoint.partition_id != descriptor.partition_id
        or checkpoint.partition_index != descriptor.partition_index
        or checkpoint.partition_count != descriptor.partition_count
        or checkpoint.unit_ids != descriptor.unit_ids
        or checkpoint.input_fingerprint != descriptor.input_fingerprint
        or checkpoint.prompt_version != N3_PROMPT_VERSION
        or checkpoint.schema_version != N3_SCHEMA_VERSION
    ):
        raise ValueError("checkpoint input or version mismatch")
    if checkpoint.result_hash != checkpoint_result_hash(checkpoint.result):
        raise ValueError("checkpoint result hash mismatch")
    result = checkpoint.result
    included = set(descriptor.unit_ids)
    expected_excluded = [
        item["id"] for item in bundle["reading_units"] if item["id"] not in included
    ]
    if (
        result.document_id != bundle["document"]["id"]
        or result.source_bundle_id != bundle["bundle_id"]
        or result.prompt_version != N3_PROMPT_VERSION
        or result.schema_version != N3_SCHEMA_VERSION
        or result.review_run_id != checkpoint.generating_run_id
        or result.input_coverage.total_unit_count != len(bundle["reading_units"])
        or result.input_coverage.included_unit_count != len(included)
        or result.input_coverage.excluded_unit_ids != expected_excluded
    ):
        raise ValueError("checkpoint result metadata mismatch")
    try:
        validate_source_references(result, bundle)
    except ValueError:
        raise ValueError("checkpoint source references invalid") from None
    rebound = result.model_copy(update={"review_run_id": run_id}, deep=True)
    try:
        validate_source_references(rebound, bundle)
    except ValueError:
        raise ValueError("checkpoint rebound source references invalid") from None
    return rebound


def run_p2a_n3(
    bundle: dict,
    profile,
    run_id: str,
    model_call: Callable,
    requested_model: str,
    target_journal: str | None = None,
    special_requirements: str | None = None,
    *,
    chunk_chars: int = 12000,
    budget: RunBudget | None = None,
    checkpoint_loader: Callable[[N3PartitionDescriptor], StoredN3Checkpoint | None] | None = None,
    on_checkpoint: Callable[[StoredN3Checkpoint], None] | None = None,
    on_result: Callable[[str, ClaimEvidenceMap], None] | None = None,
    on_trace: Callable[[CallTrace], None] | None = None,
) -> tuple[ClaimEvidenceMap, list[CallTrace]]:
    """Run only N3, reusing exact validated checkpoints when explicitly supplied."""
    units = bundle.get("reading_units") or []
    if not units:
        raise ValueError("N3 source has no readable reading units")
    quality = assess_input_readiness(bundle, "section_summary")
    if quality["status"] == "blocked":
        raise ValueError("N3 source scope is blocked")
    if profile.document_id != bundle["document"]["id"] or profile.source_bundle_id != bundle["bundle_id"]:
        raise ValueError("N3 PaperProfile source mismatch")
    validate_source_references(profile, bundle)

    budget = budget or RunBudget()
    raw_model_call = model_call
    budgeted_call = lambda stage, system, user: budget.call(stage, system, user, raw_model_call)
    descriptors = build_n3_partition_descriptors(
        bundle,
        profile,
        target_journal,
        special_requirements,
        max_chars=min(chunk_chars, 9000),
    )
    units_by_id = {item["id"]: item for item in units}
    traces: list[CallTrace] = []
    partials: list[ClaimEvidenceMap] = []
    for descriptor in descriptors:
        stored = checkpoint_loader(descriptor) if checkpoint_loader else None
        if stored is not None:
            persisted = inherit_n3_checkpoint(stored, descriptor, bundle, run_id)
            result = persisted.result
        else:
            part = [units_by_id[unit_id] for unit_id in descriptor.unit_ids]
            result = _invoke(
                "contribution-evidence-mapper",
                descriptor.user_text,
                bundle,
                run_id,
                part,
                quality,
                requested_model,
                budgeted_call,
                traces,
                ClaimEvidenceMap,
                on_trace,
            )
            persisted = _stored_checkpoint(
                run_id, descriptor, result, generation_mode="generated"
            )
        if on_checkpoint:
            on_checkpoint(persisted)
        partials.append(result)

    mapping = _merge_claim_maps(
        partials,
        profile,
        bundle,
        run_id,
        quality,
        requested_model,
        budgeted_call,
        traces,
        on_trace,
    )
    if any(
        gap.startswith("Global scalar synthesis failed")
        for gap in profile.unresolved_source_gaps
    ):
        mapping.execution_status = "limited"
        mapping.input_coverage.limitations.append("upstream_global_synthesis_failed")
    validate_source_references(mapping, bundle)
    if on_result:
        on_result("claim_evidence_map", mapping)
    return mapping, traces
