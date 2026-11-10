# P5-B2.2 人工意见幂等与报告阅读流程

## 范围

本轮只调整 P4 人工新增意见的重复提交处理，以及已有报告项目的阅读与技术核验展示。P3 来源快照、科学判定、审批规则、草稿修订和导出契约保持原样。

## 精确幂等契约

- 请求身份由同一 `project_id` 内的 `scientific_question`、`evidence_basis`、`source_refs`、`comment_body` 和 `category` 构成。文字先做 Unicode NFC，再将连续空白折叠为一个空格并去除首尾空白；来源 ID 去首尾空白、去重并排序。大小写和其他标点保留，因此语义相近但文字不同的意见不会被合并。
- 规范化对象采用固定键序、UTF-8 JSON 表示并计算 SHA-256。指纹只用于重复请求判定，不改变原始意见的正文或来源顺序。
- 新请求返回 HTTP 201；同项目同指纹的重复请求返回 HTTP 200 和最早保存的完整 `ReviewerAddedIssue`，不会写入新意见、决定或草稿修订。
- SQLite 侧表以 `(project_id, request_fingerprint)` 为主键。每次新增在 `BEGIN IMMEDIATE` 事务中查重、写入意见和登记指纹；并发请求由数据库写锁与唯一主键共同约束。
- 初始化侧表时按历史 `rowid` 顺序为已有意见建立索引。若历史存在重复意见，最早一条作为未来重复请求的返回对象；其余历史记录保持可读，不删除、不合并、不改写。

## 阅读与核验

- 打开已有项目后自动读取最新已保存的草稿，先展示可阅读的 Markdown 正文；编辑区按需展开。若没有草稿，显示明确的空状态。
- P3 问题、人工新增意见、原始 ID、来源 ID 和 CoverageGap 收纳于可展开的技术核验区。问题状态和来源类型保持明确，不改变审核动作及原文定位。
- 对人工新增意见，使用冻结 Source Bundle 中的来源索引提供 PDF 定位入口。AI 辅助状态不会被显示成人工确认。
- 草稿仍按原有接口保存修订、重新审核并导出；页面只改变呈现顺序，不自动批准、生成新草稿或调用模型。
- 用量栏只合计已知 Token；若存在 Usage 未知的调用，单独提示费用不完整。

## 浏览器前后对比

以下图片均来自公开合成 P4 浏览器夹具，不含私有论文、凭证或真实审稿正文。夹具快照保留 `live_model` 来源模式标签，但截图生成过程仅使用 Fake Model，没有供应商请求：

- [修改前：问题表单占据默认阅读位置](assets/p5-b2-2-before.png)
- [修改后：默认显示已保存报告正文](assets/p5-b2-2-after-reading.png)
- [修改后：按需展开技术审计与 CoverageGap](assets/p5-b2-2-after-audit.png)

## 修改与验收

预计修改 `backend/p4_api.py`、`backend/tests/test_p4_api.py`、`frontend/src/P4ReportPane.tsx`、`frontend/src/P4ReportPane.css`、`frontend/tests/p4-report.spec.ts` 和本文件。使用临时数据库与 Fake Model 验证并发、历史兼容、审批/修订/导出；通过浏览器截图比较阅读与审计层级。Word/WPS 兼容性仍沿用 B2.1 的未验证结论。
