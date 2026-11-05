# P3 独立证据核验（Schema 1.0）

P3 读取同一冻结链上的 P1 Source Bundle、P2-A ReviewPlan/PaperProfile/ClaimEvidenceMap、P2-B ProfessionalReviewResult、P2-C CandidateConsolidationResult 和原始 PDF。P2-C 的每个 VerificationTask 产生一个 IssueAdjudication；P3 不修改上游结果，也不写入旧版 finding_verifications 或正式报告。

## 来源与核验等级

`validate_p3_inputs` 校验 document、snapshot、Source Bundle、P2-A/B/C 运行 ID、P2-C Schema 1.1、原始候选清单、任务谱系和全部 Source Ref；PDF 可访问时重新计算实际文件的 SHA-256。哈希不符直接拒绝。PDF 缺失时保留 `pdf_unavailable`，任务判为 blocked。P2-C limited 可以进入 P3，其来源限制继续传播。

`EvidenceVerifier` 按冻结引用定位页码、坐标和 Evidence ID，再对实际 PDF 独立运行 PyMuPDF 文本提取。`pdf_text_located` 只表示引用文字在对应 PDF 区域被定位，不能证明模型的科学解释。表格、公式和图像生成保存在私有数据目录的区域 PNG，并保持 `visual_check_required`；没有人工视觉核对时不升级为已验证。`SourceObservation` 记录原文、独立 PDF 文本、方法、状态与局限；模型解释保存在单独的 `FactAssessment`。

## 反证与技术核验

`CounterEvidenceRetriever` 对全部可用 reading_units 建索引，结合原始引用、相邻正文、方法、实验、消融、讨论等章节选出上下文。`CounterEvidenceSearch` 保存已索引数量、实际提供给模型的对象、截断对象和质量限制，`absence_confirmed` 固定为 false。选中的上下文不能证明论文全文不存在某项实验。

技术检查按任务选择。指标值仅在冻结提取和独立 PDF 文本中均出现时进行十进制重算；两个值只有在数据集、指标、模型、配置和单位标签完全相同时才比较。表格数值出现不等于行列关系得到视觉确认。`mathcheck` 仅处理声明了假设的标量表达式；计算结果仍是 conditional，不证明 PDF 转录、矩阵推导或完整理论正确。外部文献、代码或数据未实际提供时记录 `external_check_unavailable`。

## 判定与数据契约

版本化 Prompt 为 `source-observation-check/v1`、`counterevidence-challenge/v1`、`technical-evidence-assessment/v1` 和 `issue-adjudication/v2`。四个职责分开调用；模型产生建议，服务端再核对引用、工具执行结果、PDF 定位和缺失性断言。模型输出非法或预算耗尽时产生保守的 inconclusive 任务结果。

`backend/p3_schema.py` 定义 VerificationSource、SourceObservation、ToolCheck、VerificationSubcheck、IssueAdjudication、EvidenceVerificationResult。子检查分别是 supported、contradicted、inconclusive 或 blocked；整合问题判定为：

| verdict | 服务端条件与含义 |
| --- | --- |
| `concern_supported` | 具体审稿询问有已定位原文依据及 supported 子检查；不等于作者错误被证明。缺失性断言不能仅凭选中上下文成立。 |
| `concern_refuted` | 所有子检查均被相同疑点的定位反证否定；结果不可报告。 |
| `concern_narrowed` | 原问题过宽，至少一个子检查有依据，且有明确修订问题；原始问题和候选谱系保留。 |
| `inconclusive` | 已有材料不足、部分检查未通过来源门槛，保留待核实事项。 |
| `blocked` | 缺少关键 PDF 或核验能力，无法完成该项检查。 |

`provisional_impact` 仅为 major_candidate、minor_candidate 或 undetermined；`report_eligibility` 独立记录限定报告、人工复核或不可报告。`human_review_status` 默认 pending。每个结果保存全部原始 Candidate ID、中心问题、Source Ref、Counterevidence Ref 和对应 P2-C 运行 ID。`validate_p3_result` 要求每个 VerificationTask 均有去向，拒绝外来引用与未执行工具 ID。

最终 `verdict_rationale` 由服务端根据实际 PDF 文本定位数、索引数和子检查状态生成；模型对工具是否执行的叙述不会被直接复制。子检查的 `actual_observations` 取自已定位的 PDF 文本；模型解释明确标为 `Model interpretation`，保存在 limitations 中。真实评测采用该确定性规则对原始模型调用的已记录结构化结果做了一次重放，未增加模型调用或改变判定状态。

## API、恢复与隐私

- `POST /api/candidate-consolidation-runs/{run_id}/evidence-verification-runs`：启动独立核验；已有运行时须显式 `{"retry": true}`，且不能覆盖运行中的任务。
- `GET /api/evidence-verification-runs/{run_id}`：总体状态、任务结果、统计和脱敏调用元数据。
- `GET /api/evidence-verification-runs/{run_id}/tasks/{task_id}`：单项判定、原文观察和工具记录。

三个端点复用现有认证。SQLite 保留运行、逐任务结果、模型调用元数据和工具结果；失败重试创建新的运行 ID，保留旧历史。模型调用表不保存请求正文、完整响应或 API Key，只保存模型标识、Prompt 版本、Token、耗时和错误类别。未知 Token 记录为未知，不按零用量解释。原始 PDF、区域 PNG、完整模型输入输出及私有评测运行文件放在被 Git 忽略的私有数据目录。生产部署仍需配置访问权限和保留期限；本阶段没有新增管理平台。

## 已知边界

自动 PDF 文本匹配不能代替人工逐页视觉核验。30,000 字符的模型上下文来自全文索引的选取，不能据此证明某内容在全文缺席。配置标签相同仍可能掩盖实验语义差异。未提供外部材料时，创新性或外部代码类问题只能标记受限。P3 不生成正式审稿报告，也不自动作出接收或拒稿建议。
