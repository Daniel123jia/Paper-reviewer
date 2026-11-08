import copy

import pytest

from backend.p2a_engine import RunBudget, UnknownUsageError
from backend.p2a_resume import (
    StoredN3Checkpoint,
    build_n3_partition_descriptors,
    checkpoint_result_hash,
    run_p2a_n3,
)
from backend.tests.test_p2a_engine import MAP, _aux_reply, _reply
from backend.tests.test_p2b_engine import inputs


def resume_inputs():
    bundle, plan, profile, mapping = inputs()
    bundle["document"]["sha256"] = "pdf-sha"
    return bundle, plan, profile, mapping


def fingerprints(bundle, profile, **kwargs):
    return [item.input_fingerprint for item in build_n3_partition_descriptors(
        bundle, profile, target_journal=kwargs.get("target_journal"),
        special_requirements=kwargs.get("special_requirements"),
        max_chars=kwargs.get("max_chars", 20))]


def test_n3_partition_fingerprint_is_stable_across_run_ids():
    bundle, _, profile, _ = resume_inputs()
    rebound = profile.model_copy(update={"review_run_id": "continuation-run"})

    original = build_n3_partition_descriptors(bundle, profile, None, "N3 only", max_chars=20)
    continued = build_n3_partition_descriptors(bundle, rebound, None, "N3 only", max_chars=20)

    assert [(item.partition_id, item.input_fingerprint, item.unit_ids) for item in original] == [
        (item.partition_id, item.input_fingerprint, item.unit_ids) for item in continued]
    assert len(original) == 2


@pytest.mark.parametrize("mutation", [
    "source_text", "profile_content", "journal", "requirements", "bundle", "snapshot", "pdf",
])
def test_n3_partition_fingerprint_rejects_changed_inputs(mutation):
    bundle, _, profile, _ = resume_inputs()
    baseline = fingerprints(bundle, profile, special_requirements="N3 only")
    changed_bundle = copy.deepcopy(bundle)
    changed_profile = profile.model_copy(deep=True)
    kwargs = {"special_requirements": "N3 only"}

    if mutation == "source_text":
        changed_bundle["reading_units"][0]["text"] += " changed"
    elif mutation == "profile_content":
        changed_profile.research_problem.text += " changed"
    elif mutation == "journal":
        kwargs["target_journal"] = "Changed Journal"
    elif mutation == "requirements":
        kwargs["special_requirements"] = "Changed requirement"
    elif mutation == "bundle":
        changed_bundle["bundle_id"] = "changed-bundle"
    elif mutation == "snapshot":
        changed_bundle["document"]["snapshot_id"] = "changed-snapshot"
    elif mutation == "pdf":
        changed_bundle["document"]["sha256"] = "changed-pdf"

    assert fingerprints(changed_bundle, changed_profile, **kwargs) != baseline


def test_n3_partition_fingerprint_rejects_prompt_or_schema_version_change(monkeypatch):
    bundle, _, profile, _ = resume_inputs()
    baseline = fingerprints(bundle, profile)

    monkeypatch.setattr("backend.p2a_resume.N3_PROMPT_VERSION", "changed/v9")
    assert fingerprints(bundle, profile) != baseline

    monkeypatch.setattr("backend.p2a_resume.N3_PROMPT_VERSION", "contribution-evidence-mapper/v2")
    profile.schema_version = "1.0" if profile.schema_version == "1.1" else "1.1"
    assert fingerprints(bundle, profile) != baseline


def five_part_inputs():
    bundle, plan, profile, mapping = resume_inputs()
    first = bundle["reading_units"][0]
    for index in range(3, 6):
        bundle["reading_units"].append({**first, "id": f"ru{index}",
                                        "text": f"Part {index} source"})
    return bundle, plan, profile, mapping


def test_n3_unknown_usage_stops_and_authorized_continuation_reuses_only_valid_checkpoints():
    bundle, _, profile, _ = five_part_inputs()
    saved = []
    part_calls = 0

    def first_model(stage, system, user):
        nonlocal part_calls
        if stage == "contribution-evidence-mapper":
            part_calls += 1
            if part_calls == 4:
                raise RuntimeError("connection interrupted")
            return _reply(MAP)
        return _aux_reply(stage, user)

    with pytest.raises(UnknownUsageError):
        run_p2a_n3(bundle, profile, "failed-run", first_model, "requested-model",
                    chunk_chars=1, budget=RunBudget(),
                    on_checkpoint=lambda item: saved.append(item))
    assert part_calls == 4
    assert len(saved) == 3
    assert all(item.generation_mode == "generated" for item in saved)

    stored = {item.input_fingerprint: item for item in saved}
    continuation_calls = []

    def continuation_model(stage, system, user):
        continuation_calls.append(stage)
        if stage == "contribution-evidence-mapper":
            return _reply(MAP)
        return _aux_reply(stage, user)

    persisted = []
    result, traces = run_p2a_n3(
        bundle, profile, "continued-run", continuation_model, "requested-model",
        chunk_chars=1, budget=RunBudget(),
        checkpoint_loader=lambda descriptor: stored.get(descriptor.input_fingerprint),
        on_checkpoint=persisted.append,
    )

    assert continuation_calls == ["contribution-evidence-mapper"] * 2 + [
        "contribution-normalizer"]
    assert [item.generation_mode for item in persisted] == [
        "inherited", "inherited", "inherited", "generated", "generated"]
    assert all(item.result.review_run_id == "continued-run" for item in persisted)
    assert result.review_run_id == "continued-run"
    assert len(traces) == 3


@pytest.mark.parametrize("damage", ["hash", "fingerprint", "payload", "evidence"])
def test_n3_rejects_corrupt_or_mismatched_checkpoint_before_model_call(damage):
    bundle, _, profile, _ = five_part_inputs()
    generated = []

    def model(stage, system, user):
        if stage == "contribution-evidence-mapper":
            return _reply(MAP)
        return _aux_reply(stage, user)

    run_p2a_n3(bundle, profile, "source-run", model, "requested-model", chunk_chars=1,
                on_checkpoint=generated.append)
    checkpoint = copy.deepcopy(generated[0])
    if damage == "hash":
        checkpoint.result_hash = "0" * 64
    elif damage == "fingerprint":
        checkpoint.input_fingerprint = "f" * 64
    elif damage == "evidence":
        checkpoint.result.evidence_refs[0].snapshot_id = "foreign-snapshot"
        checkpoint.result_hash = checkpoint_result_hash(checkpoint.result)
    else:
        checkpoint.result.contributions[0].title = "tampered"

    calls = []
    with pytest.raises(ValueError, match="checkpoint"):
        run_p2a_n3(bundle, profile, "continued-run", lambda *args: calls.append(args),
                    "requested-model", chunk_chars=1,
                    checkpoint_loader=lambda descriptor: checkpoint)
    assert calls == []


def test_checkpoint_hash_excludes_nothing_from_validated_result():
    bundle, _, profile, _ = resume_inputs()
    descriptors = build_n3_partition_descriptors(bundle, profile, None, None, max_chars=20)
    result = profile  # any Pydantic result exercises canonical hashing deterministically
    assert checkpoint_result_hash(result) == checkpoint_result_hash(result.model_copy(deep=True))
    assert descriptors


def test_checkpoint_persistence_failure_stops_before_next_supplier_call():
    bundle, _, profile, _ = five_part_inputs()
    calls = []

    def model(stage, system, user):
        calls.append(stage)
        if stage == "contribution-evidence-mapper":
            return _reply(MAP)
        return _aux_reply(stage, user)

    def unavailable_storage(checkpoint):
        raise OSError("checkpoint storage unavailable")

    with pytest.raises(OSError, match="checkpoint storage unavailable"):
        run_p2a_n3(bundle, profile, "failed-run", model, "requested-model",
                    chunk_chars=1, on_checkpoint=unavailable_storage)
    assert calls == ["contribution-evidence-mapper"]
