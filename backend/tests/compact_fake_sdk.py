"""Deterministic SDK fixture. The returned scientific prose is explicitly a simulation."""

import json
from types import SimpleNamespace as NS


class CompactFakeSDK:
    failure = None
    calls = []

    def __init__(self, *, timeout, max_retries, **kwargs):
        assert timeout == 180 and max_retries == 0
        self.chat = NS(completions=NS(create=self.create))

    def create(self, **kwargs):
        assert kwargs["stream"] and kwargs["stream_options"] == {"include_usage": True}
        data = json.loads(kwargs["messages"][1]["content"])
        self.calls.append(data)
        source = data["source"]
        checking = "draft" in data
        block = next(
            (b for b in source["blocks"] if "randomly select 10%" in b["text"]),
            source["blocks"][0],
        )
        paragraph = " ".join(block["text"].split())
        sentence = "For datasets without a standard dev set we randomly select 10% of the training data as the dev set."
        ref = dict(
            evidence_id=block["id"],
            quote=sentence if sentence in paragraph else paragraph[:160],
        )
        if self.failure == "bad_quote":
            ref["quote"] = "THIS TEXT WAS NOT IN THE SOURCE"
        issue = dict(
            severity="minor",
            question="开发集抽样对比较的影响有多大？",
            rationale="随机划分可能改变早停所选轮次。",
            impact="幅度较小的模型差异需要结合划分波动解读。",
            recommendation="说明各模型是否共享划分，并在可行时提供多划分结果。",
            certainty="needs_human_check",
            evidence=[ref],
        )
        report = dict(
            paper="【Fake SDK 模拟报告，仅供工程验收】"
            + str(source["metadata"].get("title") or "测试论文")
            + " · "
            + str(source["metadata"].get("author") or "作者未标明"),
            contribution="演示：使用预训练词向量作为卷积分类器输入，比较不同表示更新策略。",
            assessment="演示：应区分模型结构与预训练表示对分类效果的贡献。",
            strengths=["通过多项句子分类任务比较表示策略。"],
            comments=[issue],
            author_questions=["各配置的早停验证集是否一致？"],
            limitations=["未对原始训练结果或全部表格作人工复核。"],
            conclusion="模拟建议：结合实验设置核实上述问题后再形成审稿决定。",
        )
        if checking:
            report["comments"][0]["question"] = (
                "固定开发集划分后，不同表示策略的相对表现是否稳定？"
            )
            result = dict(
                checks=[
                    dict(
                        draft_index=1,
                        verdict="revised",
                        final_index=1,
                        reason="原文已说明抽样比例，保留关于共享划分及稳定性的澄清。",
                        evidence=[ref],
                    )
                ],
                report=report,
            )
            if self.failure == "missing_check":
                result["checks"] = []
        else:
            result = report
        model = kwargs["model"]
        yield NS(
            model=model,
            choices=[NS(delta=NS(content=json.dumps(result, ensure_ascii=False)))],
            usage=None,
        )
        if self.failure == "disconnect" and not checking:
            raise ConnectionError("fake interruption")
        if self.failure == "missing_usage" and checking:
            return
        yield NS(
            model=model,
            choices=[],
            usage=NS(prompt_tokens=101, completion_tokens=37, total_tokens=138),
        )
