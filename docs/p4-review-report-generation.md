# P4 专业同行评审报告生成与导出

P4 将已冻结的 P1–P3 结果组织为内部报告、作者意见草稿和编辑说明。P4 只组织与表述已有核验结果，不修改 P3 判定，也不进行新的科学核验。当前面向一审；没有修订稿和作者回复时，不推断二审回应情况。

## 来源与数据契约

`POST /api/evidence-verification-runs/{run_id}/report-projects` 加载同一来源链中的 ReviewPlan、PaperProfile、ClaimEvidenceMap、ProfessionalReviewResult、CandidateConsolidationResult、EvidenceVerificationResult、Source Bundle 和原始 PDF。服务端校验各运行 ID、文档 ID、Source Bundle ID、PDF SHA-256 及 P3 引用，再创建不可变的 P4 来源快照。项目保存 P3 Schema 版本、原始结果摘要和 `execution_mode`；`recorded_model_replay` 保持为历史响应重放，不标为新模型核验。

版本化 P4 规划/证据包 Pydantic 契约位于 `backend/p4_report_schema.py`，当前 Schema `1.1`、准入规则 `p4.1`；写作模型输出契约仍为 `1.0`：

- `ReportSourceIdentity`：document、snapshot、PDF SHA-256、Source Bundle 及 P2-A/P2-B/P2-C/P3 运行 ID。
- `EligibilityEntry`：原始 verdict、report_eligibility、human_review_status 及 P4 processing_status。
- `ReviewerDecision`：审稿人采纳、批准、修改、排除或要求复核的记录；`reviewer_approval_status` 与 P3 状态分离。
- `EvidenceReviewRecord`：人工查看 PDF 时的对象、页码、行列、数值、指标顺序、未确认配置和说明。批准报告本身不会创建此记录。
- `ExternalEvidenceReviewRecord`：外部文献、代码或数据的独立核验记录，保存材料标识、核验问题、已核查结论、限制和确认状态。`ReviewerDecision` 只能引用该记录，点击批准不会自动创建它。
- `ReviewerAddedIssue`：明确标识人工新增来源；不会冒充 P3 自动核验。
- `ReportPlan`、`ReportIssueGroup`、`ReportEvidencePack`、`ReportComment`、`ReportWritingResult`：结构化筛选、分组、写作和可追溯意见。

P4 独立使用 SQLite `p4_report_projects`、`p4_reviewer_decisions`、`p4_evidence_reviews`、`p4_external_evidence_reviews`、`p4_reviewer_added_issues`、`p4_drafts`、`p4_draft_revisions` 和 `p4_model_calls`。现有数据库初始化会增建这些表，不更改旧 `review_drafts` 或 P1–P3 表。Schema 1.1 仍可读取已保存的 1.0 计划和证据包；旧记录没有的外部核验字段按“未声明/未核验”保守解释。生产环境仍须补齐论文原文、快照及诊断数据的权限和保留期限。

## 准入与人工审核

`backend/p4_eligibility.py` 按 P3 原字段确定处理状态：支持或收窄且具备报告资格的意见为 `auto_draft_candidate`；关键证据需人工确认的为 `human_review_required`；`inconclusive` 或 `blocked` 为 `internal_only`；已反证或不可报告的为 `excluded`。作者草稿默认只选择自动候选。人工复核对象必须属于该 P3 问题、页码必须与冻结来源一致；未确认的视觉证据不能因点击“批准意见”而获得确认。人工新增意见需要独立来源说明和批准。

报告规划先构建准入清单，再按科学问题与相关 Claim 进行保守去重，保留所有 P3 Issue ID。`major_candidate` 不自动成为 Major Comment；收窄后的澄清问题保留为 Clarification。内部报告记录全部问题及状态，作者可见报告只包含准入且被选中的意见。没有准入意见时，作者草稿为空并明确提示审稿人处理待核验事项；系统不补齐固定问题数。

跨问题合组要求共享相关 Claim，并且科学质疑类型（泛化、效率/内存、数学正确性、实验比较/消融）一致；在此基础上还需问题高度相似，或共享同一具体技术对象的主要核验需求。不再因共享三词短语和 Claim 就自动合并；无法证明等价时保留为独立问题。合组保留全部来源 ID、各项观察、正反证据和待核验检查。DistilBERT 的两项 MLM-only 匹配基线问题仍是同一实验对象与核验需求，因此报告层保持合组；P3 两个原始判定仍独立保留。

对外部材料的准入单独判定：P3 已完成的 external ToolCheck 可作为已核验状态；否则，依赖先前工作结论、代码、脚本或外部数据的最终问题标记为 `critical_unresolved`，不能靠 ReviewerDecision 放行。只要求作者界定本文自身结论范围的问题，可由审稿人明确选择 `clarification_only` 并保存未核验限定后纳入；与最终问题无关的次要外部待办不会阻断合理澄清。

## 写作与验证

当前 Prompt 版本：`review-report-planning/v1`、`review-comment-writing-zh/v2`、`review-comment-writing-en/v2`、`review-report-consistency/v1`；历史 v1 写作 Prompt 保留供旧审计读取。输入 `ReportPlan` 与 `ReportEvidencePack` 使用 P4 Schema 1.1，模型写作输出继续严格使用 `ReportWritingResult` Schema 1.0。v2 写作 Prompt 明确禁止把输入的 `schema_version="1.1"` 复制到输出；若第一次响应的校验错误包含该字段的 Literal 契约错误，第二次提示会以固定脱敏文字指出 `ReportWritingResult.schema_version` 必须为 `"1.0"`，同时要求修复其余错误，不会放宽其他结构、证据或科学约束。中英文分别从同一冻结证据包撰写；生成第二语言时仅传递首语言的来源 ID 和数值集合以对齐事实，不自由翻译整篇 Markdown。内部报告按问题分片请求模型，完整证据包和全部 P3 状态仍保存在来源快照和报告计划中。连接错误不进入内容修复；每次已发送请求单独记账，未知 Token 保持 `NULL` 并终止后续请求。

服务端校验模型 JSON 与 Pydantic Schema、问题成员及分类、Evidence 归属、数字来源、禁止的内部 ID 和录用决定，并检查中英文意见的来源、数字和问题范围。模型一致性检查是辅助解释，不等于独立验证。任意自然语言的全部科学含义无法仅用确定性规则证明，生成草稿仍需审稿人逐条阅读。

## 编辑、修订和导出

前端“审稿文档”中的 P4 入口可选择 P3 运行、打开 PDF Evidence、查看三类状态、记录人工证据核验、采纳或排除意见、添加独立的人工问题，以及创建中英文草稿。打开旧草稿不会调用模型；明确点击“生成新草稿”会创建新的 draft ID，旧稿保留。保存正文新增修订并使已有批准失效，乐观锁检查 `expected_revision`。最终导出绑定精确正文 SHA-256，并要求审稿人确认当前版本及逐条意见批准。未经审核的 MD/DOCX/PDF 下载带明显 Draft 标识。

导出复用原有 `backend/exporter.py` 的 Markdown → Pandoc → DOCX/PDF 流程；P4 不改变旧版导出语义。作者报告不包含内部核验状态、原始机器 ID、模型日志或编辑私密内容。真实导出回归检查 DOCX 为可打开的 ZIP/Word 文档，PDF 为可解析页面与文本，而非更改扩展名。

## API

- `GET /api/documents/{document_id}/evidence-verification-runs`、`GET /api/documents/{document_id}/review-report-projects`：同论文来源选择。
- `POST /api/evidence-verification-runs/{run_id}/report-projects`、`GET /api/review-report-projects/{project_id}`：冻结和读取项目。
- `PATCH /api/review-report-projects/{project_id}/issues/{issue_id}/decision`、`POST .../evidence-reviews`、`POST .../external-evidence-reviews`、`POST /api/review-report-projects/{project_id}/reviewer-issues`：人工决策、PDF 视觉复核、外部材料核验与新增意见。
- `POST /api/review-report-projects/{project_id}/drafts`、`GET/PUT /api/review-report-drafts/{draft_id}`、`POST .../approve`、`GET .../export?format=md|docx|pdf&final=true|false`：生成、编辑、批准和导出。

所有接口继承现有 FastAPI 访问控制；部署时须配置实际访问密钥。P4 模型调用表仅保存模型标识、Prompt 版本、Token、耗时、状态及错误类别，不保存密钥或完整模型请求/响应。

内部报告的开篇范围由服务端依据完整 ReportPlan 确定性生成，列出原始 P3 问题数、报告主题数和限定性草稿资格数；单个分片的模型概述不会冒充全文总评。每个分片输入会标记被省略的工具结果与来源限制数量，完整材料始终保存在冻结快照中。
