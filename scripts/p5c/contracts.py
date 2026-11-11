from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from scripts.p5.contracts import PaperBudget, PaperSpec, PromptRecord


Hash = str


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Baseline(Strict):
    commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    tree: str = Field(pattern=r"^[0-9a-f]{40}$")


class ReferenceIdentity(Strict):
    path: str
    file_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    issue_ids: list[str] = Field(min_length=1)
    provenance: Literal["non_expert_source_grounded_reference"]
    frozen_before_etpaper_output: Literal[True]
    etpaper_output_viewed: Literal[False]


class Scope(Strict):
    paper_count: Literal[1]
    report_kinds: list[Literal["internal:zh", "internal:en", "author:zh", "author:en"]]
    stage_authorization: Literal["manual_per_paid_stage", "separately_authorized_full_run"]
    budget_policy: Literal["explicit_per_stage_and_paper", "explicit_per_paper"]


class VersionedManifest(Strict):
    manifest_schema_version: Literal["2.0"]
    evaluation_version: Literal["p5-c/v2.0"]
    asset_version: Literal["p5-c/v2.0-assets.1"]
    scientific_baseline: Baseline
    execution_binding: Literal["external_lock_after_final_commit"]
    requested_model: Literal["gpt-6.1-sol"]
    schema_versions: dict[str, str]
    prompt_inventory: list[PromptRecord] = Field(min_length=1)
    papers: list[PaperSpec] = Field(min_length=2, max_length=2)
    references: dict[str, ReferenceIdentity]
    scopes: dict[Literal["c1_trial", "full_p5"], Scope]
    full_p5_per_paper_budget: PaperBudget
    pricing_status: Literal["unknown"]
    external_material_policy: Literal["no_blind_reference_content_in_model_inputs"]
    reference_scoring_gate: Literal["after_result_seal"]

    @model_validator(mode="after")
    def validate_contract(self):
        paper_ids = [paper.paper_id for paper in self.papers]
        if len(set(paper_ids)) != 2 or set(paper_ids) != set(self.references):
            raise ValueError("paper and reference identities must match")
        if any(paper.group != "holdout" or paper.reference_status != "frozen"
               for paper in self.papers):
            raise ValueError("only frozen holdout papers are permitted")
        if len({record.path for record in self.prompt_inventory}) != len(self.prompt_inventory):
            raise ValueError("prompt paths must be unique")
        if set(self.scopes) != {"c1_trial", "full_p5"}:
            raise ValueError("evaluation scopes are incomplete")
        trial = self.scopes["c1_trial"]
        full = self.scopes["full_p5"]
        if (trial.report_kinds != ["internal:zh"] or
                trial.stage_authorization != "manual_per_paid_stage" or
                trial.budget_policy != "explicit_per_stage_and_paper"):
            raise ValueError("C1 trial scope changed")
        if (full.report_kinds != ["internal:zh", "internal:en", "author:zh", "author:en"] or
                full.stage_authorization != "separately_authorized_full_run" or
                full.budget_policy != "explicit_per_paper"):
            raise ValueError("full P5 scope changed")
        return self


class ExecutionLock(Strict):
    lock_schema_version: Literal["1.0"]
    evaluation_version: Literal["p5-c/v2.0"]
    asset_version: Literal["p5-c/v2.0-assets.1"]
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    execution_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    execution_tree: str = Field(pattern=r"^[0-9a-f]{40}$")
