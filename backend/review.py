import json
import re
from collections import Counter


def parse_model_json(text):
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return json.loads(cleaned)


def pick_review_evidence(blocks, limit=30):
    """Sample source blocks across the whole manuscript, favoring claims and results."""
    by_document = {}
    for row in blocks:
        by_document.setdefault(row["document_id"], {}).setdefault(row["page_index"], []).append(row)
    if not by_document:
        return []
    pools = {}
    for document_id, pages in by_document.items():
        pool = []
        for page_blocks in pages.values():
            scored = sorted(page_blocks, key=lambda row: (
                bool(re.search(r"\b(?:table|figure|result|improv|baseline|experiment|conclusion|theorem|proof|limitation|assumption)\b|\([1-9][0-9]?\)",
                               row["excerpt"], re.IGNORECASE)),
                len(row["excerpt"])), reverse=True)
            pool.extend(scored[:5])
        pools[document_id] = pool
    budgets = {document_id: min(len(pool), max(1, limit // len(pools)))
               for document_id, pool in pools.items()}
    remaining = max(0, limit - sum(budgets.values()))
    while remaining and any(budgets[document_id] < len(pool) for document_id, pool in pools.items()):
        for document_id, pool in pools.items():
            if remaining and budgets[document_id] < len(pool):
                budgets[document_id] += 1
                remaining -= 1
    selected = []
    for document_id, pool in pools.items():
        count = budgets[document_id]
        selected.extend(pool[int(index * len(pool) / count)] for index in range(count))
    return selected[:limit]


def screen_candidates(candidates, checks, available):
    check_by_index = {item.get("index"): item for item in checks if isinstance(item, dict)}
    findings = []
    for index, candidate in enumerate(candidates[:3]):
        if not isinstance(candidate, dict):
            continue
        ids = candidate.get("evidence_ids", [])
        ids = ids if isinstance(ids, list) else []
        valid = list(dict.fromkeys(item for item in ids if isinstance(item, str) and item in available))
        check = check_by_index.get(index, {})
        verdict = check.get("verdict", "uncertain")
        support_assessment = str(check.get("support_assessment", "")).strip()[:2000]
        if len(valid) != len(ids) or not valid or verdict == "not_supported":
            status = "rejected"
        elif verdict == "supported" and support_assessment:
            status = "needs_independent_verification"
        else:
            status = "uncertain"
        findings.append({
            "title": str(candidate.get("title", "候选问题"))[:180],
            "concern": str(candidate.get("concern", ""))[:2000],
            "evidence": [{"evidence_id": item, "document_id": available[item]["document_id"],
                          "page_index": available[item]["page_index"]} for item in valid],
            "evidence_level": "source_located" if valid else "unlocated",
            "verification_status": status,
            "reviewer_decision": "pending",
            "support_assessment": support_assessment,
            "counterargument": str(check.get("counterargument", "独立反证未给出说明"))[:2000],
        })
    return findings


def retrieve_counterevidence(blocks, cited_ids, limit=20):
    """Keep the cited passage and rank other source blocks by distinctive shared terms."""
    rows = list(blocks)
    cited = [row for row in rows if row["id"] in cited_ids]
    if not cited:
        return []
    stop = {"about", "after", "their", "there", "these", "which", "where", "would",
            "using", "between", "other", "with", "from", "this", "that", "into",
            "each", "same", "model", "paper", "also", "have", "been", "were"}
    words = lambda value: set(word.lower() for word in re.findall(r"[A-Za-z]{4,}", value)
                              if word.lower() not in stop)
    cited_terms = Counter(word for row in cited for word in words(row["excerpt"]))
    frequency = Counter(word for row in rows for word in words(row["excerpt"]))
    weights = {word: count / (1 + frequency[word]) for word, count in cited_terms.items()}
    cited_pages = {(row["document_id"], row["page_index"]) for row in cited}

    def score(row):
        nearby = any(row["document_id"] == doc and abs(row["page_index"] - page) <= 1
                     for doc, page in cited_pages)
        return sum(weights.get(word, 0) for word in words(row["excerpt"])) + (0.8 if nearby else 0)

    selected = cited[:limit]
    counts = Counter((row["document_id"], row["page_index"]) for row in selected)
    for document_id in dict.fromkeys(row["document_id"] for row in rows):
        if len(selected) >= limit:
            break
        if any(row["document_id"] == document_id for row in selected):
            continue
        best = max((row for row in rows if row["document_id"] == document_id), key=score)
        selected.append(best)
        counts[(best["document_id"], best["page_index"])] += 1
    selected_ids = {row["id"] for row in selected}
    ranked = sorted((row for row in rows if row["id"] not in selected_ids), key=score, reverse=True)
    for row in ranked:
        if len(selected) >= limit:
            break
        key = (row["document_id"], row["page_index"])
        if counts[key] >= 5 or score(row) <= 0:
            continue
        selected.append(row)
        counts[key] += 1
    return selected


def suggest_formula_links(blocks):
    """Find explicit rewrite/split references followed by the next numbered equation."""
    rows = list(blocks)
    equations = {}
    for index, row in enumerate(rows):
        match = re.search(r"\(([1-9][0-9]?)\)\s*$", row["excerpt"])
        if match and len(row["excerpt"]) < 400:
            equations.setdefault((row["document_id"], int(match.group(1))), (index, row))
    links = []
    seen = set()
    for context_index, context in enumerate(rows):
        text = " ".join(context["excerpt"].split())
        if not re.search(r"\b(split(?:ting)?|expand(?:ing)?|rewrite|rewritten|decompos(?:e|ing))\b", text,
                         re.IGNORECASE):
            continue
        for ref in re.finditer(r"\beq(?:uation)?\.?\s*\(([1-9][0-9]?)\)", text, re.IGNORECASE):
            number = int(ref.group(1))
            before = equations.get((context["document_id"], number))
            after = equations.get((context["document_id"], number + 1))
            if not before or not after or not (before[0] <= context_index < after[0]):
                continue
            if after[1]["page_index"] - context["page_index"] > 1:
                continue
            key = (before[1]["id"], after[1]["id"])
            if key not in seen:
                seen.add(key)
                links.append({"from_equation": number, "to_equation": number + 1,
                              "left": before[1], "right": after[1], "relation": context})
    return links
