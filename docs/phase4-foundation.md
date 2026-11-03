# P4 离线公式核查基础（未完成 P4 全阶段）

2026-11-03。

## 已运行

- 增加 SymPy 1.14.0 与 NumPy 1.26.4。表达式通过受限 Python AST 解析，允许基础算术、整数幂以及少数数学函数；拒绝属性访问、代码调用和过大幂。没有将模型输出交给 SymPy 的 `parse_expr` 执行，因为[官方文档](https://docs.sympy.org/latest/modules/parsing.html)说明它会使用 `eval`，不适合未清理的输入。
- `verify_equation_pair` 先做符号化简，再尝试确定性 NumPy 数值样本寻找反例。结果区分 `equivalent`、`counterexample` 和 `inconclusive`，记录假设、化简差、样本、工具版本。
- 公式核查 API 可关联候选问题已有 Evidence ID，并把输入、工具结果保存到 SQLite。由于原文转录还未独立确认，记录始终标为 `unconfirmed`，不会将候选问题升级为“已证实错误”。
- 新增审稿任务级的跨证据离线核查：从当前任务的原稿、修订稿或回复中选两处不同 Evidence ID，检索并回看原文，输入标量表达式和可选符号假设，保存 SymPy / NumPy 结果及两处证据引用。跨任务或无效证据被拒绝。核查记录刷新后仍可查看并跳回 PDF；运行按钮明确标注不消耗中转站 Token。
- 对数值表达式，还会逐一检查输入的数字是否出现在对应的两处原文摘录；即使全部找到，也不会据此认定表格行列配对或结论语义正确。

## 真实 PDF 样例

《Self-Attention with Relative Position Representations》PDF 第 2 页公式 (4) 对应 `ev_c1c08dc5e672_1_19`；第 3 页公式 (5) 对应 `ev_c1c08dc5e672_2_19`。将其分配律关系缩成标量表达式后，`q*(k+a)/sqrt(d)` 与 `(q*k+q*a)/sqrt(d)` 在 `d>0` 假设下化简差为 0。故意漏掉第二项的 `q` 时，`(q*k+a)/sqrt(d)` 得到差 `a*(q-1)/sqrt(d)` 和可复现数值反例。这只验证了分配律关系；没有证明论文的矩阵、转置、张量维度与实现都正确。

同一论文第 4 页 Table 1 的数值块 `ev_c1c08dc5e672_3_0` 与文字结论块 `ev_c1c08dc5e672_3_2` 也在浏览器中关联核查：基础模型 EN-DE BLEU 的 `26.8-26.5` 与文中声称的 `0.3` 相等。此核查只覆盖一个表格差值，未证明所有实验结论。

## 测试与遗留

- `.venv/bin/python -m pytest backend/tests -q`：31 passed。包括正确恒等式、已知错误、正数假设、危险表达式拒绝、跨任务证据拒绝、实验数字原文匹配，以及工具结果持久化且不自动提升候选问题状态。
- `PLAYWRIGHT_BASE_URL=http://127.0.0.1:5175 npx playwright test tests/cross-formula.spec.ts`：1 passed。真实 PDF 的公式关系和表格差值均完成浏览器检索、选择证据、离线计算、刷新恢复。原文跳转在浏览器中显示第 3 页公式 (5) 高亮。前端构建通过。
- 还缺自动从 PDF 建立跨公式关系、独立核对转录和矩阵维度、算法与理论条件核对，以及全面的实验表格与结论交叉验证。因此 P4 未完成，不应据此生成正式数学错误意见。
