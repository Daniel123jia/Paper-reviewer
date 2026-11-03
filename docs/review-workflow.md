# 当前审稿工作流

本文档按当前真实代码说明审稿链路。它不是理想工作流设计。

## 1. PDF 上传与解析

- API：`POST /api/documents`
- 实现：`backend.app.upload_document`
- 输入：最大 50 MB 的 PDF。
- 处理：计算 SHA-256，用 PyMuPDF `page.get_text("blocks", sort=True)` 抽取文字块。有 PDF TOC 时使用 TOC，否则用 `section_from_excerpt` 识别有限章节标题。
- 输出：`documents`、`evidence` 记录和私有 `files/<document_id>.pdf`。

影像页只记录 `needs_ocr`，当前不执行 OCR。

## 2. Evidence ID

Evidence ID 在上传时生成：

```text
ev_<snapshot UUID 前 12 位>_<0-based page_index>_<PyMuPDF block_index>
```

它与解析快照绑定。重新解析同一 PDF 可能产生不同前缀，Evidence ID 本身只表示位置，不证明审稿判断成立。

## 3. 首轮证据选择

- 实现：`backend.review.pick_review_evidence(blocks, limit=30)`
- 调用：`backend.app.run_review`

算法：

1. 按文档和页分组。
2. 每页根据是否命中 `table|figure|result|improv|baseline|experiment|conclusion|theorem|proof|limitation|assumption` 或公式编号，再按文本长度排序。
3. 每页最多进入候选池 5 块。
4. 在不同文档间平均分配总上限 30，将余额轮流分配。
5. 对各文档候选池等距取样。
6. Prompt 中每块最多使用前 900 字符。

对诊断论文 *Self-Attention with Relative Position Representations*，快照共 99 块、5 页，每页最多 5 块，因此当前算法实际选中 25 块，而非 30 块。历史快照清单见 `tests/fixtures/shaw2018-review-snapshot.json`。

## 4. 候选问题生成

- 阶段：`propose`
- 实现：`backend.app.run_review` -> `review_model_call`
- Prompt 位置：`backend/app.py` 中 `role=system`。

System Prompt 要求模型只根据抽样片段提出 `0 到 3` 个候选问题，输出带 Evidence ID 的 JSON。返回后代码再执行 `candidates = candidates[:3]`。当前没有单独的全文理解、创新性、方法、实验、图表、文献等多维度候选发现阶段。

## 5. 第一轮 Challenge

- 阶段：`challenge`
- Prompt 位置：`backend/app.py`
- 输入：最多 3 个候选问题和与 `propose` 相同的抽样证据。
- 输出：`supported | not_supported | uncertain`、`support_assessment`和 `counterargument`。

`backend.review.screen_candidates` 校验 Evidence ID，并把问题存为 `needs_independent_verification`、`uncertain` 或 `rejected`。Challenge 不是外部事实核验，它使用与首轮相同的配置模型。

## 6. Deep Challenge

- API：`POST /api/review-findings/{finding_id}/deep-verifications`
- 实现：`run_finding_verification`
- 检索：`backend.review.retrieve_counterevidence`

该步保留引用块，再根据共享英文词和相邻页加分，从全部证据中最多取 20 块。Prompt 要求查找最强反证和合理解释，返回 `supported_clarification | not_supported | uncertain`。

结果中显式记录：该步只是扩展原文检索后的模型反证，不构成数学或实验事实的独立程序证明。

## 7. 数学、实验与算法核验

- `verify_equation_pair`：对用户输入的受限标量表达式进行 SymPy 等价化简和确定性数值反例；原文转录默认未确认。
- `audit_paired_bleu_table`：仅解析特定的 EN-DE/EN-FR、base/big、Absolute/Relative 格式，核对表格差值与正文声称。
- `check_matmul_description`：仅匹配特定英文 `parallel multiplications of ... matrices` 描述，检查内维和总输出元素数。

这些核验由独立 API 或界面操作触发，不会自动扩展首轮候选问题池。

## 8. 状态

`review_findings` 保存：

- `evidence_level`: `source_located | unlocated`
- `verification_status`: `needs_independent_verification | uncertain | rejected`
- `reviewer_decision`: 当前创建时固定为 `pending`
- `counterargument`、`support_assessment`

Deep Challenge 的 verdict 存在 `finding_verifications.result`，没有合并成一个通用的技术核验状态模型。

## 9. 中文报告

- 实现：`backend.drafts.compose_draft`
- `report`：列出当前运行的所有 finding、Deep Challenge 和已保存工具核验。
- `opinion`：仅接受未被拒绝、有原文证据、已完成 Deep Challenge 且 verdict 为 `supported_clarification` 的 finding。

中文内容是 Python 模板组合，不会在创建草稿时另外调用模型进行全文综合。

## 10. 英文翻译与导出

英文草稿使用 `backend/app.py` 中的翻译 Prompt，要求保留 Evidence ID、数字、表格、LaTeX 和章节，禁止增加新事实。代码仅强制校验翻译前后 Evidence ID 集合一致。

`backend.exporter.render_export` 导出：

- Markdown：直接 UTF-8。
- DOCX：Pandoc + 动态参考 DOCX。
- PDF：Pandoc 生成 HTML，再用 `frontend/scripts/render_pdf.mjs` 调用 Chromium 打印。

## Prompt 索引

| Prompt | 位置 |
|---|---|
| 候选问题生成 | `backend/app.py`, `run_review`, `stage=propose` |
| 第一轮反证 | `backend/app.py`, `run_review`, `stage=challenge` |
| 深化反证 | `backend/app.py`, `run_finding_verification`, `stage=deep_challenge` |
| 论文对话 | `backend/app.py`, `chat`, `instructions` |
| 英文翻译 | `backend/app.py`, `create_draft` |
| 中文报告模板 | `backend/drafts.py`, `compose_draft` |

当前 `review_model_calls` 只保存阶段、请求模型名、Token、费用摘要、耗时和状态，不保存完整 Prompt 或原始模型响应。
