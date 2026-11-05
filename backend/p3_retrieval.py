"""Search every available reading unit before asking a model to challenge a concern."""

import math
import re
from collections import Counter
from pathlib import Path

from pydantic import Field

from backend.p2a_schema import StrictModel, source_reference_index
from backend.p3_schema import CounterEvidenceSearch


PROMPT_VERSION = "counterevidence-challenge/v1"
PROMPT = (Path(__file__).with_name("prompts") / "p3" /
          "counterevidence-challenge.v1.txt")
STOPWORDS = {"about", "after", "against", "also", "could", "does", "each", "from",
             "have", "into", "only", "other", "paper", "reported", "same", "such",
             "that", "their", "there", "these", "this", "under", "using", "were",
             "what", "when", "where", "which", "with", "would", "evidence", "claim",
             "result", "results", "model", "models", "author", "authors", "source"}


def _terms(text):
    return {term for term in re.findall(r"[a-z0-9]{3,}|[\u4e00-\u9fff]{2,}",
                                        text.casefold()) if term not in STOPWORDS}


class ContextEntry(StrictModel):
    source_id: str
    source_type: str
    section_id: str | None
    page_index: int
    text: str
    reason: str


class SourceContextPack(StrictModel):
    verification_task_id: str
    entries: list[ContextEntry]
    search: CounterEvidenceSearch


class CounterEvidenceChallenge(StrictModel):
    supporting_source_ids: list[str]
    contradicting_source_ids: list[str]
    author_answer_source_ids: list[str]
    author_answer_assessment: str
    unresolved_questions: list[str]
    source_interpretation: str
    source_limitations: list[str]
    evidence_level: str = Field(default="model_interpretation", pattern="^model_interpretation$")


class CounterEvidenceRetriever:
    def __init__(self, bundle, mapping, profile):
        self.bundle = bundle
        self.canonical = source_reference_index(bundle)
        self.claims = {item.id: item for item in mapping.claims}
        self.profile = profile
        self.units = [unit for unit in bundle["reading_units"] if unit["id"] in self.canonical]
        self.index = {unit["id"]: unit for unit in self.units}
        self.index.update({item["id"]: item for item in bundle["tables"] + bundle["formulas"]
                           if item["id"] in self.canonical})
        self.index.update({item["evidence_id"]: item for item in bundle["body"]
                           if item["evidence_id"] in self.canonical})
        self.index.update({item["id"]: item for item in bundle["sections"] + bundle["figures"]
                           if item["id"] in self.canonical})
        self.terms = {source_id: _terms(self._text(item)) for source_id, item in self.index.items()}
        self.frequency = Counter(term for terms in self.terms.values() for term in terms)

    @staticmethod
    def _text(item):
        if "structured_rows" in item:
            return "\n".join([item.get("caption") or ""] +
                             [" | ".join(str(cell or "") for cell in row)
                              for row in item.get("structured_rows", [])])
        return item.get("text") or item.get("title") or item.get("caption") or ""

    def retrieve(self, task, issue, max_chars=30000):
        if max_chars < 1000:
            raise ValueError("context limit must allow at least one source paragraph")
        quoted = [ref.source_id for ref in task.source_refs_to_check +
                  task.counterevidence_refs_to_check]
        query = " ".join([issue.title, issue.central_question, issue.suspected_problem,
                          *issue.verification_questions,
                          *(self.claims[claim_id].statement for claim_id in task.related_claim_ids)])
        query_terms = _terms(query)
        scores = {source_id: sum(math.log((len(self.index) + 1) /
                    (self.frequency[term] + 1)) + 1 for term in terms & query_terms)
                  for source_id, terms in self.terms.items()}
        adjacent = set()
        positions = {unit["id"]: index for index, unit in enumerate(self.units)}
        for source_id in quoted:
            if source_id in positions:
                position = positions[source_id]
                adjacent.update(self.units[index]["id"] for index in
                                (position - 1, position + 1)
                                if 0 <= index < len(self.units))
            elif source_id in self.canonical:
                evidence_ids = set(self.canonical[source_id].evidence_ids)
                adjacent.update(unit["id"] for unit in self.units
                                if evidence_ids.intersection(unit["evidence_ids"]))
        important_sections = {section["id"] for section in self.bundle["sections"]
            if re.search(r"method|experiment|ablation|appendix|discussion|limitation|evaluation|result",
                         section["title"], re.I)}
        section_first = set()
        for unit in self.units:
            if unit.get("section_id") in important_sections and unit["section_id"] not in section_first:
                section_first.add(unit["section_id"])
                adjacent.add(unit["id"])
        ordered = sorted((source_id for source_id in self.index
                          if source_id in quoted or self.canonical[source_id].object_type in
                          {"reading_unit", "table", "formula"}), key=lambda source_id: (
            source_id in quoted, source_id in adjacent, scores[source_id],
            self.canonical[source_id].object_type in {"table", "formula"}), reverse=True)
        entries = []
        used = 0
        truncated = []
        for source_id in ordered:
            item = self.index[source_id]
            full = self._text(item).strip()
            if not full:
                continue
            priority = source_id in quoted or source_id in adjacent
            if not priority and scores[source_id] <= 0:
                continue
            if used >= max_chars:
                break
            allowed = max_chars - used
            if len(full) > allowed:
                if allowed < 200 and entries:
                    break
                truncated.append(source_id)
            excerpt = full[:allowed]
            entries.append(ContextEntry(source_id=source_id,
                source_type=self.canonical[source_id].object_type,
                section_id=item.get("section_id"),
                page_index=self.canonical[source_id].page_index,
                text=excerpt, reason="original_ref" if source_id in quoted else
                    "adjacent_or_section" if source_id in adjacent else "full_text_search"))
            used += len(excerpt)
        selected = {item.source_id for item in entries}
        limits = list(dict.fromkeys(self.bundle.get("limitations", []) +
            self.profile.unresolved_source_gaps + self.profile.input_coverage.limitations))
        if any(page["status"] != "text_extracted" for page in self.bundle["pages"]):
            limits.append("At least one PDF page has no reliable extracted text")
        if set(quoted) - selected:
            limits.append("At least one original or counterevidence reference was not included in the model context")
        if len(selected) < len(self.index):
            limits.append("Model context is selected from a complete text search, not the full PDF")
        search = CounterEvidenceSearch(verification_task_id=task.verification_task_id,
            indexed_unit_count=len(self.units),
            selected_source_ids=[item.source_id for item in entries],
            truncated_source_ids=truncated, source_quality_limits=list(dict.fromkeys(limits)))
        return SourceContextPack(verification_task_id=task.verification_task_id,
                                 entries=entries, search=search)


def validate_challenge(raw, pack):
    result = CounterEvidenceChallenge.model_validate(raw)
    allowed = set(pack.search.selected_source_ids)
    if not set(result.supporting_source_ids + result.contradicting_source_ids +
               result.author_answer_source_ids).issubset(allowed):
        raise ValueError("counterevidence challenge cites a source outside its context pack")
    return result
