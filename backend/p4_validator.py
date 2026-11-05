"""Report writing gates. Natural-language correctness still needs reviewer approval."""

import re

from backend.p4_report_schema import ReportEvidencePack, ReportPlan, ReportWritingResult


def numeric_claims(text):
    text = re.sub(r"(?m)^#{2,6}\s+\d+\.\s*", "", text)
    text = re.sub(r"(?:\b(?:PDF\s+)?pages?|\b(?:Tables?|Sections?|Figures?)|第)\s*"
                  r"\d+\s*[-–—~至]\s*\d+\s*(?:页)?",
                  "", text, flags=re.I)
    without_locators = re.sub(
        r"(?:\b(?:table|section|figure|fig\.?|equation|eq\.?|pages?)|表|图|公式|第)\s*"
        r"\(?\d+(?:\.\d+)*\)?", "", text, flags=re.I)
    return set(re.findall(r"(?<![a-z0-9_])\d+(?:\.\d+)?(?![a-z0-9_])",
                          without_locators.casefold()))


def allowed_author_numbers(pack: ReportEvidencePack):
    source = " ".join(pack.paper_facts)
    for issue in pack.issues:
        source += " " + " ".join(issue.observed_statements + issue.external_verified_findings)
    for item in pack.reviewer_added:
        source += " " + item.evidence_basis
    return numeric_claims(source)


def allowed_internal_numbers(pack: ReportEvidencePack):
    source = " ".join(pack.paper_facts)
    for issue in pack.issues:
        source += " " + " ".join(issue.observed_statements + issue.actual_tool_results +
            issue.author_explanations + issue.outstanding_checks +
            issue.external_verified_findings +
            [issue.scientific_question, issue.original_question,
             issue.revised_question or ""])
    for item in pack.reviewer_added:
        source += " " + item.evidence_basis
    return numeric_claims(source)


def validate_writing(result: ReportWritingResult, plan: ReportPlan,
                     pack: ReportEvidencePack):
    if result.language != plan.language or result.report_kind != plan.report_kind:
        raise ValueError("report language or kind differs from the frozen plan")
    expected = {group.group_id: group for group in plan.groups}
    if {item.report_comment_id for item in result.comments} != set(expected):
        raise ValueError("writer omitted or invented a planned report group")
    issue_index = {item.source_issue_id: item for item in pack.issues}
    manual_index = {item.issue_id: item for item in pack.reviewer_added}
    if plan.report_kind != "author":
        unknown = numeric_claims(result.general_assessment + " " + " ".join(
            item.comment_body + " " + item.author_action_requested
            for item in result.comments)) - allowed_internal_numbers(pack)
        if unknown:
            raise ValueError(f"internal report introduces unsupported numbers: {sorted(unknown)}")
    for comment in result.comments:
        group = expected[comment.report_comment_id]
        if set(comment.source_issue_ids) != set(group.source_issue_ids) or \
                comment.comment_category != group.category:
            raise ValueError("writer changed issue grouping or impact category")
        manual = any(item_id in manual_index for item_id in group.source_issue_ids)
        if comment.source != ("reviewer_added" if manual else "p3"):
            raise ValueError("writer changed the origin of a reviewer comment")
        if comment.scientific_question != group.scientific_question:
            raise ValueError("writer changed the frozen scientific question")
        refs = {ref for issue_id in group.source_issue_ids for ref in
                (issue_index[issue_id].source_refs + issue_index[issue_id].counterevidence_refs
                 if issue_id in issue_index else manual_index[issue_id].source_refs)}
        if not set(comment.evidence_refs).issubset(refs):
            raise ValueError("writer cited another issue or paper's source")
        if comment.reviewer_approval_status != "pending":
            raise ValueError("model cannot approve a report comment")
        if len(comment.comment_body.strip()) < 50 or not comment.author_action_requested.strip():
            raise ValueError("generic or incomplete report comment")
        if re.search(r"\b(?:more experiments are needed|novelty is limited|method is unclear)\b",
                     comment.comment_body, re.I):
            raise ValueError("generic review template needs a specific scientific object")
        clarification_only = [issue_index[issue_id] for issue_id in group.source_issue_ids
                              if issue_id in issue_index and
                              issue_index[issue_id].external_dependency_status ==
                              "clarification_only"]
        if clarification_only:
            if not re.search(r"\b(?:not checked|unverified|not independently verified)\b|"
                             r"未核查|未核验|未独立验证",
                             comment.uncertainty_qualification, re.I):
                raise ValueError("clarification-only external dependency needs an explicit limitation")
            if re.search(r"\b(?:prior work|previous work|literature|cited study|repository|"
                         r"external data)\b.{0,80}\b(?:has |have )?(?:proven|demonstrated|"
                         r"established|confirmed|shown)\b", comment.comment_body, re.I):
                raise ValueError("unchecked external fact cannot enter clarification-only wording")
        if plan.report_kind == "author":
            validate_author_body(comment.comment_body, pack)
            for issue_id in group.source_issue_ids:
                if issue_id not in issue_index:
                    continue
                issue = issue_index[issue_id]
                if issue.verdict not in {"concern_supported", "concern_narrowed"}:
                    raise ValueError("unverified P3 issue entered author report")
                if issue.verdict == "concern_narrowed" and issue.revised_question != \
                        issue.scientific_question:
                    raise ValueError("narrowed question was replaced with original accusation")
    if plan.report_kind == "author":
        validate_author_body(result.general_assessment, pack)
    return result


def validate_author_body(body: str, pack: ReportEvidencePack):
    if re.search(r"\b(?:ru_ev_[a-z0-9_]*|ev_[a-z0-9_]+|consolidated_[a-z0-9_]+|"
                 r"verify_[a-z0-9_]+|source_bundle|model interpretation|internal model log)\b",
                 body, re.I):
        raise ValueError("author report contains internal source identifiers or logs")
    if re.search(r"\b(?:recommend acceptance|recommend rejection|accept this paper|reject this paper|"
                 r"confirmed author error|proven error)\b|建议录用|建议拒稿|已证实作者错误", body, re.I):
        raise ValueError("report invents a publication decision or confirmed author error")
    unknown = numeric_claims(body) - allowed_author_numbers(pack)
    if unknown:
        raise ValueError(f"author report introduces unsupported numbers: {sorted(unknown)}")


def validate_bilingual_pair(left: ReportWritingResult, right: ReportWritingResult):
    if left.report_kind != right.report_kind or left.language == right.language:
        raise ValueError("bilingual reports must have the same kind and different languages")
    a = {item.report_comment_id: item for item in left.comments}
    b = {item.report_comment_id: item for item in right.comments}
    if set(a) != set(b):
        raise ValueError("Chinese and English reports cover different issues")
    for identifier in a:
        x, y = a[identifier], b[identifier]
        if (set(x.source_issue_ids) != set(y.source_issue_ids) or
                set(x.evidence_refs) != set(y.evidence_refs) or
                x.comment_category != y.comment_category or
                x.scientific_question != y.scientific_question or
                numeric_claims(x.comment_body) != numeric_claims(y.comment_body)):
            raise ValueError("Chinese and English comments changed evidence, numbers or scope")
