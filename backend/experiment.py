"""Conservative numeric audit for explicitly paired BLEU table rows and prose claims."""

from decimal import Decimal
import re


ROW = re.compile(
    r"(?P<model>[^\n]+\((?P<group>base|big)\))\n"
    r"(?P<method>Absolute|Relative) Position Representations\n"
    r"(?P<de>\d+\.\d+)\n(?P<fr>\d+\.\d+)(?:\n|$)", re.IGNORECASE)
CLAIM = re.compile(r"\bby\s+(?P<base>\d+\.\d+)\s+and\s+(?P<big>\d+\.\d+)\s+BLEU\s+for\s+the\s+base\s+and\s+big\b",
                   re.IGNORECASE)


def audit_paired_bleu_table(table_excerpt: str, claim_excerpt: str) -> dict:
    """Return a result only when both row labels and claim ordering are explicit."""
    result = {"status": "inconclusive", "checks": [], "parser_version": "paired-bleu-v1",
              "limitation": "只核对提取文字中的表格差值和声明，尚未核对 PDF 视觉排版、统计显著性或实验设计。"}
    lines = [line.strip() for line in table_excerpt.splitlines() if line.strip()]
    if not {"EN-DE BLEU", "EN-FR BLEU"}.issubset(set(lines)):
        return result
    rows = {}
    for match in ROW.finditer("\n".join(lines) + "\n"):
        key = (match.group("group").lower(), match.group("method").lower())
        if key in rows:
            return result
        rows[key] = (Decimal(match.group("de")), Decimal(match.group("fr")))
    if set(rows) != {(group, method) for group in ("base", "big") for method in ("absolute", "relative")}:
        return result

    normalized = " ".join(claim_excerpt.split())
    spans = {}
    for language, metric in (("English-to-German", "EN-DE BLEU"), ("English-to-French", "EN-FR BLEU")):
        found = re.search(rf"For\s+{language}\b(.*?)(?=For\s+English-to-|$)", normalized, re.IGNORECASE)
        if not found or not (claim := CLAIM.search(found.group(1))):
            return result
        spans[metric] = {group: Decimal(claim.group(group)) for group in ("base", "big")}

    checks = []
    for metric_index, metric in enumerate(("EN-DE BLEU", "EN-FR BLEU")):
        for group in ("base", "big"):
            baseline = rows[(group, "absolute")][metric_index]
            relative = rows[(group, "relative")][metric_index]
            calculated = relative - baseline
            claimed = spans[metric][group]
            checks.append({"metric": metric, "group": group, "baseline": str(baseline),
                           "relative": str(relative), "calculated": str(calculated),
                           "claimed": str(claimed), "matches": calculated == claimed})
    result["checks"] = checks
    result["status"] = "consistent" if all(item["matches"] for item in checks) else "numeric_mismatch"
    return result
