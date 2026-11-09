import hashlib
import json

import pytest

from backend.p4_writer import PROMPTS, WRITING_VERSIONS, render_markdown, write_report
from backend.tests.test_p4_validator import case
from backend.tests.test_p4_planner import snapshot
from backend.tests.test_p4_api import seeded_p4_client
from backend.p4_planner import build_report_plan
from backend.p4_report_schema import ReportComment, ReportWritingResult


def test_writer_uses_frozen_plan_then_advisory_consistency(tmp_path):
    plan, pack, writing = case(tmp_path)
    stages = []

    def model(stage, version, system, user):
        stages.append((stage, version))
        if stage.startswith("writing-"):
            assert plan.groups[0].group_id in user
            assert "P3 verdict" in system
            return {"content": writing.model_dump_json()}
        return {"content": json.dumps({"acceptable": True, "unsupported_facts": [],
            "state_promotions": [], "refuted_accusations": [],
            "language_or_scope_changes": []})}

    result = write_report(plan, pack, model)
    assert [stage for stage, _ in stages] == ["writing-1-attempt-1", "consistency-1-attempt-1"]
    markdown = render_markdown(plan, pack, result)
    assert "Points Requiring Clarification" in markdown
    assert "PDF page 1" in markdown
    assert "ru1" not in markdown
    assert "inconclusive" not in markdown


def test_empty_author_report_has_no_model_calls_and_internal_preserves_issue(tmp_path):
    client, _ = seeded_p4_client(tmp_path)
    frozen = snapshot(tmp_path, client)
    author, author_pack = build_report_plan(frozen, [], [], [], "author", "zh")
    writing = write_report(author, author_pack,
        lambda *_: (_ for _ in ()).throw(AssertionError("no model call")))
    assert writing.comments == []
    assert "没有自动准入" in render_markdown(author, author_pack, writing)
    internal, internal_pack = build_report_plan(frozen, [], [], [], "internal", "en")
    assert internal_pack.issues[0].verdict == "inconclusive"
    assert internal.selected_issue_ids


def test_advisory_consistency_rejection_stops_report(tmp_path):
    plan, pack, writing = case(tmp_path)

    def model(stage, version, system, user):
        if stage.startswith("writing-"):
            return {"content": writing.model_dump_json()}
        return {"content": json.dumps({"acceptable": False,
            "unsupported_facts": ["invented experiment"], "state_promotions": [],
            "refuted_accusations": [], "language_or_scope_changes": []})}

    with pytest.raises(ValueError, match="unsupported wording"):
        write_report(plan, pack, model)


def test_failed_consistency_never_marks_writing_as_validated_partial(tmp_path):
    plan, pack, writing = case(tmp_path)
    partials = []

    def model(stage, version, system, user):
        if stage.startswith("writing-"):
            return {"content": writing.model_dump_json()}
        return {"content": json.dumps({"acceptable": False,
            "unsupported_facts": ["unsupported"], "state_promotions": [],
            "refuted_accusations": [], "language_or_scope_changes": []})}

    with pytest.raises(ValueError, match="unsupported wording"):
        write_report(plan, pack, model, validated_partial_callback=partials.append)

    assert partials == []


def test_internal_chunks_preserve_all_distinct_issue_groups(tmp_path):
    client, _ = seeded_p4_client(tmp_path, reportable=True)
    frozen = snapshot(tmp_path, client)
    plan, pack = build_report_plan(frozen, [], [], [], "internal", "en")
    second_group = plan.groups[0].model_copy(deep=True)
    second_group.group_id = "report_group_second"
    second_group.source_issue_ids = ["consolidated_second"]
    plan.groups.append(second_group)
    plan.selected_issue_ids.append("consolidated_second")
    second_issue = pack.issues[0].model_copy(deep=True)
    second_issue.source_issue_id = "consolidated_second"
    pack.issues.append(second_issue)
    pack.groups.append(second_group)
    stages = []

    def model(stage, version, system, user):
        stages.append(stage)
        if stage.startswith("consistency-"):
            return {"content": json.dumps({"acceptable": True, "unsupported_facts": [],
                "state_promotions": [], "refuted_accusations": [],
                "language_or_scope_changes": []})}
        payload = json.loads(user)
        assert len(payload["plan"]["groups"]) == 1
        assert len(payload["evidence_pack"]["issues"]) == 1
        group = payload["plan"]["groups"][0]
        comment = ReportComment(report_comment_id=group["group_id"],
            source_issue_ids=group["source_issue_ids"], source="p3",
            title_zh="具体问题", title_en="Specific question",
            comment_body="Could the authors clarify the specific method claim and the evidence used to support its interpretation?",
            scientific_question=group["scientific_question"],
            evidence_refs=[], author_action_requested="Clarify the method claim and its source scope.",
            comment_category=group["category"], uncertainty_qualification="Unverified")
        return {"content": ReportWritingResult(prompt_version=version, language="en",
            report_kind="internal", general_assessment="Authors present a method.",
            comments=[comment]).model_dump_json()}

    result = write_report(plan, pack, model)
    assert [item.report_comment_id for item in result.comments] == [
        plan.groups[0].group_id, second_group.group_id]
    assert stages == ["writing-1-attempt-1", "consistency-1-attempt-1",
                      "writing-2-attempt-1", "consistency-2-attempt-1"]
    pack.source_limitations += ["table table_ev_x structure_incomplete",
                                "figure figure_ev_y detected_unverified"]
    report = render_markdown(plan, pack, result)
    assert "2 P3 verification issues in 2 review topics" in report
    assert "coverage_gap_" not in report
    assert "table_ev_x" not in report and "figure_ev_y" not in report
    assert "table-structure limitation records" in report


def test_interruption_keeps_only_the_previously_consistency_validated_chunk(tmp_path):
    client, _ = seeded_p4_client(tmp_path, reportable=True)
    frozen = snapshot(tmp_path, client)
    plan, pack = build_report_plan(frozen, [], [], [], "internal", "en")
    second_group = plan.groups[0].model_copy(deep=True)
    second_group.group_id = "report_group_second"
    second_group.source_issue_ids = ["consolidated_second"]
    plan.groups.append(second_group)
    plan.selected_issue_ids.append("consolidated_second")
    second_issue = pack.issues[0].model_copy(deep=True)
    second_issue.source_issue_id = "consolidated_second"
    pack.issues.append(second_issue)
    pack.groups.append(second_group)
    partials = []

    def model(stage, version, system, user):
        if stage == "writing-2-attempt-1":
            raise RuntimeError("simulated interruption")
        if stage.startswith("consistency-"):
            return {"content": json.dumps({"acceptable": True,
                "unsupported_facts": [], "state_promotions": [],
                "refuted_accusations": [], "language_or_scope_changes": []})}
        payload = json.loads(user)
        group = payload["plan"]["groups"][0]
        comment = ReportComment(report_comment_id=group["group_id"],
            source_issue_ids=group["source_issue_ids"], source="p3",
            title_zh="具体问题", title_en="Specific question",
            comment_body="Could the authors clarify the method claim and its evidence scope?",
            scientific_question=group["scientific_question"], evidence_refs=[],
            author_action_requested="Clarify the method claim.",
            comment_category=group["category"], uncertainty_qualification="Unverified")
        return {"content": ReportWritingResult(prompt_version=version, language="en",
            report_kind="internal", general_assessment="Authors present a method.",
            comments=[comment]).model_dump_json()}

    with pytest.raises(RuntimeError, match="simulated interruption"):
        write_report(plan, pack, model, validated_partial_callback=partials.append)

    assert len(partials) == 1
    assert partials[0]["comments"][0]["report_comment_id"] == plan.groups[0].group_id


def test_second_language_receives_fact_alignment_without_translation(tmp_path):
    plan, pack, english = case(tmp_path)
    chinese = english.model_copy(deep=True)
    chinese.language = "zh"
    chinese.prompt_version = "review-comment-writing-zh/v2"
    chinese.comments[0].comment_body = "请作者明确方法的具体主张及原文依据，并说明这一主张的适用范围。"

    def model(stage, version, system, user):
        if stage.startswith("writing-"):
            payload = json.loads(user)
            assert payload["bilingual_alignment"][plan.groups[0].group_id]["evidence_refs"] == \
                chinese.comments[0].evidence_refs
            return {"content": english.model_dump_json()}
        return {"content": json.dumps({"acceptable": True, "unsupported_facts": [],
            "state_promotions": [], "refuted_accusations": [],
            "language_or_scope_changes": []})}

    assert write_report(plan, pack, model, chinese).comments[0].evidence_refs == \
        chinese.comments[0].evidence_refs


def test_invalid_first_chunk_is_rewritten_before_next_issue(tmp_path):
    plan, pack, writing = case(tmp_path)
    stages = []

    def model(stage, version, system, user):
        stages.append(stage)
        if stage.startswith("writing-"):
            candidate = writing.model_copy(deep=True)
            if stage.endswith("attempt-1"):
                candidate.comments[0].comment_body += " The accuracy was 91.7."
            return {"content": candidate.model_dump_json()}
        return {"content": json.dumps({"acceptable": True, "unsupported_facts": [],
            "state_promotions": [], "refuted_accusations": [],
            "language_or_scope_changes": []})}

    assert write_report(plan, pack, model).comments[0].comment_body == \
        writing.comments[0].comment_body
    assert stages == ["writing-1-attempt-1", "writing-1-attempt-2",
                      "consistency-1-attempt-2"]


@pytest.mark.parametrize("language", ["zh", "en"])
def test_current_writing_prompt_separates_input_1_1_from_output_1_0(language):
    version = WRITING_VERSIONS[language]
    assert version == f"review-comment-writing-{language}/v2"
    prompt = (PROMPTS / f"review-comment-writing-{language}.v2.txt").read_text()
    assert 'ReportWritingResult.schema_version must be exactly the string "1.0"' in prompt
    assert 'ReportPlan and ReportEvidencePack may have schema_version "1.1"' in prompt
    assert f'prompt_version="{version}"' in prompt


@pytest.mark.parametrize("language", ["zh", "en"])
def test_current_writing_prompt_preserves_alignment_and_strict_comment_contract(language):
    prompt = (PROMPTS / f"review-comment-writing-{language}.v2.txt").read_text()
    assert "If bilingual_alignment is supplied" in prompt
    assert "use exactly its evidence_refs and numeric_claims" in prompt
    assert "do not translate the other language's wording" in prompt
    assert "general_assessment_numeric_claims" in prompt
    exact_keys = (
        "report_comment_id, source_issue_ids, source, title_zh, title_en, "
        "comment_body, scientific_question, evidence_refs, "
        "author_action_requested, comment_category, uncertainty_qualification, "
        "reviewer_approval_status"
    )
    assert f"ReportComment must contain exactly these keys: {exact_keys}" in prompt
    assert "Do not include verdict, report_eligibility, human_review_status" in prompt
    assert "other P3 metadata as ReportComment keys" in prompt


@pytest.mark.parametrize(("language", "expected_hash"), [
    ("zh", "54bd8271db6a3840bd224216924e4c6b0f31d946c4ccf7989162ddda2d434ae1"),
    ("en", "c1c811303bb7ee715bfd674124446c61a54efc3c8a6c5c1ea2bc7ceb80c22446"),
])
def test_historical_v1_writing_prompt_is_unchanged(language, expected_hash):
    prompt = PROMPTS / f"review-comment-writing-{language}.v1.txt"
    assert hashlib.sha256(prompt.read_bytes()).hexdigest() == expected_hash


def test_schema_version_mismatch_gets_exact_repair_and_then_succeeds(tmp_path):
    plan, pack, writing = case(tmp_path)
    assert plan.schema_version == pack.schema_version == "1.1"
    stages = []

    def model(stage, version, system, user):
        stages.append((stage, version, system))
        if stage == "writing-1-attempt-1":
            candidate = writing.model_dump(mode="json")
            candidate["schema_version"] = "1.1"
            candidate["prompt_version"] = version
            return {"content": json.dumps(candidate)}
        if stage == "writing-1-attempt-2":
            assert 'ReportWritingResult.schema_version must be exactly "1.0"' in system
            assert 'Do not copy schema_version "1.1" from ReportPlan or ReportEvidencePack' in system
            candidate = writing.model_dump(mode="json")
            candidate["prompt_version"] = version
            return {"content": json.dumps(candidate)}
        return {"content": json.dumps({"acceptable": True, "unsupported_facts": [],
            "state_promotions": [], "refuted_accusations": [],
            "language_or_scope_changes": []})}

    result = write_report(plan, pack, model)

    assert result.schema_version == "1.0"
    assert result.prompt_version == "review-comment-writing-en/v2"
    assert [(stage, version) for stage, version, _ in stages] == [
        ("writing-1-attempt-1", "review-comment-writing-en/v2"),
        ("writing-1-attempt-2", "review-comment-writing-en/v2"),
        ("consistency-1-attempt-2", "review-report-consistency/v1"),
    ]


def test_two_schema_version_mismatches_fail_closed(tmp_path):
    plan, pack, writing = case(tmp_path)
    stages = []

    def model(stage, version, system, user):
        stages.append(stage)
        candidate = writing.model_dump(mode="json")
        candidate["schema_version"] = "1.1"
        candidate["prompt_version"] = version
        return {"content": json.dumps(candidate)}

    with pytest.raises(ValueError):
        write_report(plan, pack, model)

    assert stages == ["writing-1-attempt-1", "writing-1-attempt-2"]


def test_fixing_version_does_not_bypass_other_evidence_validation(tmp_path):
    plan, pack, writing = case(tmp_path)
    stages = []

    def model(stage, version, system, user):
        stages.append(stage)
        candidate = writing.model_dump(mode="json")
        candidate["schema_version"] = "1.1" if stage.endswith("attempt-1") else "1.0"
        candidate["prompt_version"] = version
        candidate["comments"][0]["evidence_refs"] = ["foreign-paper-source"]
        return {"content": json.dumps(candidate)}

    with pytest.raises(ValueError, match="another issue"):
        write_report(plan, pack, model)

    assert stages == ["writing-1-attempt-1", "writing-1-attempt-2"]
