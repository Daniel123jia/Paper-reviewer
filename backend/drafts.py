"""Two evidence-grounded review templates; no new scientific claims are inferred here."""

import sympy as sp

from backend.mathcheck import parse_expression


MODES = {"first_review": "一审", "own_rereview": "二审（本人复审）", "new_reviewer": "二审（新增审稿人）"}
STATUSES = {"needs_independent_verification": "待独立验证", "uncertain": "证据尚不充分",
            "rejected": "已排除"}


def math_latex(expression, assumptions):
    return sp.latex(parse_expression(expression, assumptions, {}))


def source_label(source):
    return f'PDF 第 {source["page_index"] + 1} 页 [{source["evidence_id"]}]'


def compose_draft(kind, paper, mode, findings, formula_checks, experiment_audits, algorithm_audits):
    if kind == "opinion":
        accepted = [item for item in findings if item["verification_status"] != "rejected"
                    and item.get("deep") and item["deep"].get("verdict") == "supported_clarification"
                    and item["evidence"]]
        lines = ["# 正式审稿意见", "", f"论文：{paper['filename']}", f"审稿轮次：{MODES[mode]}", "",
                 "## 致编辑与作者", "", "以下意见基于可定位的论文原文与独立反证复核。", "",
                 "## 主要意见", ""]
        if not accepted:
            lines.append("目前没有经独立反证支持、可纳入正式意见的具体问题。")
        for index, item in enumerate(accepted, 1):
            lines += [f"### {index}. {item['title']}", "", item["concern"], "",
                      "建议作者明确相关定义、适用范围或实现细节。此处提出澄清要求，不将未验证的技术疑点表述为已证实错误。", "",
                      "原文依据：" + "；".join(source_label(source) for source in item["evidence"]), ""]
        lines += ["## 结语", "", "上述意见旨在使论证范围和可复核性更加清楚。", ""]
        return "\n".join(lines)

    lines = ["# 内部完整审稿报告", "", f"论文：{paper['filename']}", f"文件 SHA-256：{paper['sha256']}",
             f"审稿轮次：{MODES[mode]}", "", "## 范围与判断", "",
             f"本报告记录 {len(findings)} 条候选问题及工具核查结果。候选问题不自动等同于论文错误；原文坐标与验证状态应分别核对。", "",
             "## 候选问题核查", ""]
    if not findings:
        lines += ["本轮抽样没有形成候选问题。这不表示论文没有问题。", ""]
    for index, item in enumerate(findings, 1):
        deep = item.get("deep") or {}
        lines += [f"### {index}. {item['title']}", "", item["concern"], "",
                  f"证据等级：{item['evidence_level']}；验证状态：{STATUSES.get(item['verification_status'], item['verification_status'])}", "",
                  f"证据支持关系：{item['support_assessment']}", "",
                  f"反证与合理解释：{item['counterargument']}", ""]
        if deep:
            lines += [f"扩展原文反证：{deep.get('verdict', 'uncertain')}。{deep.get('rebuttal', '')}", "",
                      f"仍需检查：{deep.get('missing_checks', '')}", ""]
        lines += ["引用：" + "；".join(source_label(source) for source in item["evidence"]), ""]

    lines += ["## 数学与技术核查", ""]
    if not (formula_checks or experiment_audits or algorithm_audits):
        lines += ["尚无已保存的程序核查记录。", ""]
    for check in formula_checks:
        result = check["result"]
        lines += ["### 标量表达式关系", "", f"工具结果：{result['status']}；原文转录：未确认。", "",
                  f"$${math_latex(check['lhs'], check['assumptions'])} = {math_latex(check['rhs'], check['assumptions'])}$$", "",
                  "原文：" + "；".join(source_label(source) for source in check["sources"]), ""]
        if result.get("counterexample"):
            lines += [f"数值反例：`{result['counterexample']}`。这只针对输入表达式，不证明论文有错。", ""]
    for audit in experiment_audits:
        lines += ["### 实验数值与正文", "", f"核查状态：{audit['result']['status']}。", "",
                  "| 指标 | 模型 | 表格差值 | 正文声称 | 一致 |", "|---|---|---:|---:|---|" ]
        for item in audit["result"]["checks"]:
            lines.append(f"| {item['metric']} | {item['group']} | {item['calculated']} | {item['claimed']} | {'是' if item['matches'] else '否'} |")
        lines += ["", "原文：" + source_label(audit["table_source"]) + "；" + source_label(audit["claim_source"]), ""]
    for audit in algorithm_audits:
        lines += ["### 算法矩阵维度", "", f"核查状态：{audit['result']['status']}。", ""]
        for item in audit["result"]["checks"]:
            lines += [f"- {item['parallel_count']} 次：({item['left_shape'][0]} × {item['left_shape'][1]}) × "
                      f"({item['right_shape'][0]} × {item['right_shape'][1]})；总输出元素 {item['total_output_elements']}。"
                      f"{source_label(item['source'])}"]
        lines += ["", "该核查不证明训练内存或完整空间复杂度。", ""]

    lines += ["## 证据附录", ""]
    seen = set()
    for item in findings:
        for source in item["evidence"]:
            if source["evidence_id"] not in seen:
                seen.add(source["evidence_id"])
                lines += [f"### {source_label(source)}", "", source["excerpt"][:1200], ""]
    return "\n".join(lines)
