from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from scripts.p5.contracts import EvaluationManifest, PaperBudget, PaperSpec
from scripts.p5.environment import prompt_inventory, public_environment_snapshot


BASELINE = "c4b613f486b4db6f118dc0ef99b7c0c103676843"
BUDGET = PaperBudget(max_calls=90, max_input_tokens=1_200_000,
                     max_output_tokens=220_000, max_duration_seconds=10_800,
                     max_retries=2, max_cost_usd=None,
                     price_source="unknown; token and call ceilings enforced")


def _paper(paper_id: str, title: str, group: str, url: str, digest: str, version: str,
           appendix: bool, tables: bool = True, formulas: bool = True,
           figures: bool = True) -> PaperSpec:
    return PaperSpec(paper_id=paper_id, title=title, group=group,
                     official_pdf_url=url, pdf_sha256=digest, version=version,
                     reference_status="frozen", has_appendix=appendix,
                     has_tables=tables, has_formulas=formulas, has_figures=figures)


def build_manifest() -> EvaluationManifest:
    papers = [
        _paper("shaw2018", "Self-Attention with Relative Position Representations",
               "historical_regression", "https://arxiv.org/pdf/1803.02155",
               "8086e2e5e139fd576631ce0ec4560c5ee66225bec260d631a3e8fe7f1f13d50b",
               "arXiv 1803.02155 / NAACL 2018", False),
        _paper("distilbert2019", "DistilBERT, a distilled version of BERT",
               "historical_regression", "https://arxiv.org/pdf/1910.01108",
               "a70c78e2b4db85bfee7696e19829571a3b05f69c616db5c039f6f4eb33b1a0b6",
               "arXiv 1910.01108", False),
        _paper("fasttext2016", "Bag of Tricks for Efficient Text Classification",
               "historical_regression", "https://arxiv.org/pdf/1607.01759",
               "6cbcf620c2537ed54dfb9ed0d843a83689b82dc41dcd305bfb2e82425d4d7906",
               "arXiv 1607.01759", False),
        _paper("sardet100k-neurips2024",
               "SARDet-100K: Towards Open-Source Benchmark and ToolKit for Large-Scale SAR Object Detection",
               "holdout", "https://proceedings.neurips.cc/paper_files/paper/2024/file/e7eb8128eb26eafbe901348df1dbacdc-Paper-Conference.pdf",
               "3f4e6cddbf32524853f3493a5e7975490b8ffeae274cf139acf2b576736998b9",
               "NeurIPS 2024 final proceedings", True),
        _paper("sam-iclr2021", "Sharpness-Aware Minimization for Efficiently Improving Generalization",
               "holdout", "https://arxiv.org/pdf/2010.01412",
               "73ace22a61c52152fc84a94239cb3ea5f9fa9e753aa8ef5995f277365e25b764",
               "arXiv 2010.01412v3 / ICLR 2021", True),
    ]
    environment = public_environment_snapshot()
    return EvaluationManifest(
        baseline_commit=BASELINE, code_commit=BASELINE,
        created_at=datetime.now(timezone.utc),
        operating_system=environment["operating_system"],
        python_version=environment["python_version"],
        node_version=environment["node_version"] or "not_available",
        schema_versions={"source_bundle": "1.0", "p2a": "1.1", "p2b": "1.0",
                         "p2c": "1.1", "p3": "1.1/p3.1", "p4": "1.1/p4.1"},
        prompt_inventory=prompt_inventory(Path("backend/prompts")),
        requested_model=environment["requested_model"], actual_model=None,
        provider_label=environment["model_endpoint"],
        run_parameters={
            "p1_p4_core_frozen_at": BASELINE,
            "fresh_model_execution": False,
            "fresh_model_blocker": "model_credential_not_configured",
            "pandoc": environment["pandoc_version"],
            "playwright_chromium": environment["playwright_chromium"],
        }, papers=papers, budgets={paper.paper_id: BUDGET for paper in papers},
        paper_id_aliases={
            "sardet-100k-neurips2024": "sardet100k-neurips2024",
            "sam-arxiv-2010-01412v3": "sam-iclr2021",
        },
        chatgpt_benchmark_status="pending")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the public P5 frozen manifest")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = build_manifest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "papers": len(manifest.papers),
                      "prompts": len(manifest.prompt_inventory)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
