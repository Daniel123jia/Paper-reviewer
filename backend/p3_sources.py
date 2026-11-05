"""Validate the frozen chain and locate cited material in the actual PDF."""

import hashlib
import os
import re
from pathlib import Path

import fitz

from backend.p2a_schema import source_reference_index
from backend.p2c_engine import validate_consolidation_result
from backend.p2c_normalizer import validate_consolidation_inputs
from backend.p3_schema import SourceObservation, VerificationSource


def validate_p3_inputs(bundle, plan, profile, mapping, review, consolidation, pdf_path):
    """Reject mixed snapshots and tampered PDFs before any model or tool call."""
    validate_consolidation_inputs(bundle, plan, profile, mapping, review,
        consolidation.understanding_run_id, consolidation.professional_review_run_id,
        review.execution_status)
    validate_consolidation_result(consolidation, bundle, mapping)
    document = bundle["document"]
    if consolidation.execution_status not in {"completed", "limited"} or \
            consolidation.schema_version != "1.1" or \
            consolidation.document_id != document["id"] or \
            consolidation.snapshot_id != document["snapshot_id"] or \
            consolidation.pdf_sha256 != document.get("sha256") or \
            consolidation.source_bundle_id != bundle["bundle_id"] or \
            consolidation.inventory.understanding_run_id != consolidation.understanding_run_id or \
            consolidation.inventory.professional_review_run_id != consolidation.professional_review_run_id:
        raise ValueError("P2-C result and frozen Source Bundle identity mismatch")
    if [item.model_dump() for item in consolidation.inventory.candidates] != \
            [item.model_dump() for item in review.candidates]:
        raise ValueError("P2-C original candidates differ from the frozen P2-B result")
    issues = {item.consolidated_issue_id: item for item in consolidation.consolidated_issues}
    canonical = source_reference_index(bundle)
    task_ids = set()
    for task in consolidation.verification_tasks:
        if task.verification_task_id in task_ids:
            raise ValueError("duplicate P2-C verification task")
        task_ids.add(task.verification_task_id)
        issue = issues[task.consolidated_issue_id]
        if (task.central_question != issue.central_question or
                task.related_claim_ids != issue.related_claim_ids or
                task.original_candidate_ids != issue.source_candidate_ids or
                task.original_document_snapshot != {
                    "document_id": document["id"], "snapshot_id": document["snapshot_id"],
                    "pdf_sha256": document.get("sha256"), "source_bundle_id": bundle["bundle_id"]}):
            raise ValueError("P2-C verification task lineage or snapshot mismatch")
        if any(ref.source_id not in canonical for ref in
               task.source_refs_to_check + task.counterevidence_refs_to_check):
            raise ValueError("P2-C task cites foreign Evidence ID")
    path = Path(pdf_path) if pdf_path else None
    if not path or not path.is_file():
        return "pdf_unavailable"
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if not document.get("sha256") or digest != document["sha256"]:
        raise ValueError("actual PDF SHA-256 does not match the frozen snapshot")
    return "sha256_matched"


def _tokens(text):
    return set(re.findall(r"[\w]+", text.casefold()))


def _text_located(excerpt, extracted):
    terms = _tokens(excerpt)
    if not terms:
        return False
    if len(terms) < 4:
        return bool(terms.intersection(_tokens(extracted))) and \
            " ".join(excerpt.casefold().split()) in " ".join(extracted.casefold().split())
    return len(terms.intersection(_tokens(extracted))) / len(terms) >= 0.65


class EvidenceVerifier:
    """New PDF text pass, separate from the immutable P1 Source Bundle."""

    def __init__(self, bundle, pdf_path, region_dir=None):
        self.bundle = bundle
        self.canonical = source_reference_index(bundle)
        self.objects = {}
        for kind, key, items in (
            ("reading_unit", "id", bundle["reading_units"]),
            ("evidence", "evidence_id", bundle["body"]),
            ("section", "id", bundle["sections"]),
            ("table", "id", bundle["tables"]),
            ("formula", "id", bundle["formulas"]),
            ("figure", "id", bundle["figures"]),
        ):
            self.objects.update({item[key]: (kind, item) for item in items})
        self.pdf = fitz.open(pdf_path) if pdf_path and Path(pdf_path).is_file() else None
        self.region_dir = Path(region_dir) if region_dir else None
        self.sources = {}

    def close(self):
        if self.pdf:
            self.pdf.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _excerpt(self, kind, item):
        if kind == "table":
            rows = item.get("structured_rows") or item.get("cells") or []
            return "\n".join([item.get("caption") or ""] +
                             [" | ".join(str(cell or "") for cell in row) for row in rows]).strip()
        if kind == "figure":
            return item.get("caption") or ""
        return item.get("text") or item.get("title") or ""

    def _bbox(self, kind, item, reference):
        box = item.get("visual_bbox") if kind == "figure" else item.get("bbox")
        if box:
            return [float(value) for value in box]
        for evidence_id in reference.evidence_ids:
            evidence = self.objects.get(evidence_id)
            if evidence and evidence[1].get("bbox"):
                return [float(value) for value in evidence[1]["bbox"]]
        return None

    def _region(self, page, bbox):
        if not bbox:
            return None
        box = fitz.Rect(bbox) & page.rect
        if box.is_empty:
            return None
        pixmap = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), clip=box, alpha=False)
        raw = pixmap.tobytes("png")
        digest = hashlib.sha256(raw).hexdigest()
        if self.region_dir:
            self.region_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            path = self.region_dir / f"{digest}.png"
            if not path.exists():
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(raw)
        return digest

    def observe(self, source_id, task_id):
        if source_id not in self.canonical:
            raise ValueError("foreign Evidence ID")
        reference = self.canonical[source_id]
        kind, item = self.objects[source_id]
        excerpt = self._excerpt(kind, item)
        bbox = self._bbox(kind, item, reference)
        page_index = reference.page_index
        limitations = []
        region_hash = None
        if not self.pdf:
            status = "pdf_unavailable"
            method = "frozen_bundle_only"
            observed = ""
            limitations.append("Original PDF unavailable; frozen extraction is not an independent check")
        elif page_index >= len(self.pdf):
            status = "pdf_text_mismatch"
            method = "frozen_bundle_plus_pdf_text"
            observed = ""
            limitations.append("Source page is outside the actual PDF")
        else:
            page = self.pdf[page_index]
            clip = fitz.Rect(bbox) & page.rect if bbox else page.rect
            observed = page.get_text(clip=clip)
            method = "frozen_bundle_plus_pdf_text"
            if kind in {"table", "formula", "figure"}:
                status = "visual_check_required"
                region_hash = self._region(page, bbox)
                limitations.append("PDF region generated; layout/transcription still needs visual review")
            elif _text_located(excerpt, observed):
                status = "pdf_text_located"
                limitations.append("Text location does not establish the scientific claim")
            else:
                status = "pdf_text_mismatch"
                limitations.append("Frozen text was not reliably matched in the PDF region")
        source = VerificationSource(source_id=source_id, source_type=kind,
            document_id=reference.document_id, snapshot_id=reference.snapshot_id,
            page_index=page_index, bbox=bbox, evidence_ids=reference.evidence_ids,
            source_method=method, source_verification_status=status,
            relevant_excerpt=excerpt, region_image_sha256=region_hash,
            limitations=limitations)
        self.sources[source_id] = source
        identifier = hashlib.sha256(f"{task_id}|{source_id}".encode()).hexdigest()[:20]
        observation = SourceObservation(observation_id=f"observation_{identifier}",
            verification_task_id=task_id, related_source_ref_ids=[source_id],
            document_id=reference.document_id, snapshot_id=reference.snapshot_id,
            page_index=page_index, bbox=bbox, original_excerpt=excerpt,
            observed_statement=observed[:2500], observation_type="source_text",
            source_check_method=("pdf_text_extraction" if self.pdf else "frozen_bundle_only"),
            source_check_status=status, limitations=limitations)
        return source, observation
