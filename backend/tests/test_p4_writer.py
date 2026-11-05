import json

import pytest

from backend.p4_writer import render_markdown, write_report
from backend.tests.test_p4_validator import case
from backend.tests.test_p4_planner import snapshot
from backend.tests.test_p4_api import seeded_p4_client
from backend.p4_planner import build_report_plan


def test_writer_uses_frozen_plan_then_advisory_consistency(tmp_path):
    plan, pack, writing = case(tmp_path)
    stages = []

    def model(stage, version, system, user):
        stages.append((stage, version))
        if stage == "writing":
            assert plan.groups[0].group_id in user
            assert "P3 verdict" in system
            return {"content": writing.model_dump_json()}
        return {"content": json.dumps({"acceptable": True, "unsupported_facts": [],
            "state_promotions": [], "refuted_accusations": [],
            "language_or_scope_changes": []})}

    result = write_report(plan, pack, model)
    assert [stage for stage, _ in stages] == ["writing", "consistency"]
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
        if stage == "writing":
            return {"content": writing.model_dump_json()}
        return {"content": json.dumps({"acceptable": False,
            "unsupported_facts": ["invented experiment"], "state_promotions": [],
            "refuted_accusations": [], "language_or_scope_changes": []})}

    with pytest.raises(ValueError, match="unsupported wording"):
        write_report(plan, pack, model)
