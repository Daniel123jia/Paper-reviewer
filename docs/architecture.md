# 当前系统架构

本文档描述仓库中已实现的代码，不包含计划中的全文理解、多维度审稿或 Skills 工作流。

## 技术栈

- 前端：React 19、TypeScript 5、Vite 7、PDF.js、React Markdown、KaTeX。
- 后端：FastAPI、PyMuPDF、SQLite、OpenAI Python SDK（OpenAI 兼容 Chat Completions）。
- 离线核验：SymPy、NumPy 和基于正则的实验/算法解析器。
- 导出：python-docx 生成参考 DOCX，Pandoc 生成 DOCX/HTML，Playwright Chromium 将 HTML 渲染为 PDF。
- 测试：pytest、FastAPI TestClient、Playwright。

## 主要模块

| 路径 | 当前职责 |
|---|---|
| `backend/app.py` | FastAPI 应用、SQLite Schema、PDF 上传与解析、Evidence API、对话、审稿调用、深化反证、工具核验、草稿与导出 API |
| `backend/source_bundle.py` | 从已有 Evidence 快照和私有 PDF 构建 v1.0 结构候选，保留原 Evidence ID 与坐标 |
| `backend/parse_quality.py` | 按页、章节、表格、公式、图注报告解析状态及不能确认的部分 |
| `backend/review.py` | 模型 JSON 解析、首轮证据选择、候选问题筛查、深化反证检索、公式关联建议 |
| `backend/drafts.py` | 中文“内部完整报告”和“正式审稿意见”的固定模板 |
| `backend/mathcheck.py` | 限制语法的标量表达式符号等价和数值反例 |
| `backend/experiment.py` | 针对明确表格行与正文声明的 BLEU 差值核对 |
| `backend/algorithmcheck.py` | 针对特定英文矩阵乘法描述的维度检查 |
| `backend/exporter.py` | Markdown、DOCX 和 PDF 导出 |
| `backend/access.py` | 可选的单账号访问口令与 Cookie |
| `scripts/compare_parsers.py` | 隔离比较 PyMuPDF blocks、PyMuPDF4LLM、Docling；仅输出元数据摘要 |
| `frontend/src/App.tsx` | 论文上传、文档列表、PDF 阅读、搜索和证据高亮 |
| `frontend/src/ReviewPane.tsx` | 审稿任务、候选问题与分析状态 |
| `frontend/src/ChatPane.tsx` | 论文对话与 SSE 流式回答 |
| `frontend/src/*AuditPane.tsx` | 公式、算法和实验离线核验界面 |
| `frontend/src/DeepVerificationPane.tsx` | 单个候选问题的深化反证 |
| `frontend/src/DraftPane.tsx` | 报告生成、编辑和下载 |

## 运行时数据

`create_app()` 在指定数据目录中创建 `review.db` 和 `files/`。当前没有独立迁移框架，Schema 由 `backend/app.py` 中的 `CREATE TABLE IF NOT EXISTS` 语句建立。

主要表：

- `documents`、`evidence`：PDF 元数据、解析页、章节与文字块。
- `conversations`、`messages`、`model_calls`：论文对话和用量。
- `review_cases`、`review_runs`、`review_findings`、`review_model_calls`：审稿任务、运行、候选问题与模型调用摘要。
- `finding_verifications`：深化反证结果。
- `equation_checks`、`cross_formula_checks`、`experiment_audits`、`algorithm_audits`：工具核验记录。
- `review_drafts`、`draft_model_calls`：报告草稿与英文翻译用量。

数据目录被 `.gitignore` 排除，不应将论文、SQLite 数据库或模型调用记录提交到公开仓库。

## 数据流

```text
PDF upload
  -> PyMuPDF text blocks
  -> documents + evidence + private PDF file
  -> PDF reader/search/chat/review APIs
  -> sampled review prompt
  -> candidate findings + model countercheck
  -> optional deep countercheck and offline checks
  -> fixed Chinese draft template
  -> optional model translation to English
  -> Markdown/DOCX/PDF export
```

论文对话与审稿任务共用 `documents/evidence`，但对话消息不会自动转入审稿候选问题。

P1 新增两个只读接口：`GET /api/documents/{id}/source-bundle` 与 `GET /api/documents/{id}/parse-quality`。它们在请求时从现有 Evidence、私有 PDF 生成视图，不新增数据库表或迁移，也不改变上述 Baseline 数据流。当前结构结果为候选，双栏阅读顺序、图表和公式关联尚须核验；见 `docs/p1-source-bundle-schema.md`。

## 配置和边界

- `.env.local` 会在导入 `backend.app` 时加载，并优先覆盖同名 shell 环境变量。
- 模型密钥只由后端读取，前端不持有密钥。
- 当前代码为本地工作台，没有公网 TLS、反向代理、通用多用户系统或权限隔离。
