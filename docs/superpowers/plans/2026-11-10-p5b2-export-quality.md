# P5-B2.1 导出质量实施计划

**目标：** 修复 P4 DOCX/PDF 的中文字体元数据、技术附录表格分页、长标识断行及页眉页脚稳定性，同时保持 Markdown 内容、DRAFT/正式导出隔离和历史行为。

**实现原则：** 在现有 `backend/exporter.py` 与 `frontend/scripts/render_pdf.mjs` 上做局部增强。DOCX 使用 Pandoc 后处理；PDF 使用确定性表格分类和针对性 CSS。测试先行，所有模型路径保持零网络。

---

### Task 1：建立失败回归测试

**文件：**
- 新建 `backend/tests/test_exporter.py`
- 修改 `backend/tests/test_p4_drafts_api.py`

1. 添加包含中文、公式、短表、长表、长 Issue/Evidence ID 的导出样例。
2. 断言 DOCX 具体文本运行具备东亚字体、语言和提示元数据，表头重复、数据行不可拆分。
3. 断言 PDF 完整包含所有标识和正文，长表允许按行分页且末页不出现明显孤立内容。
4. 断言 Markdown 原文不变，DRAFT 与正式导出权限不变。
5. 先运行测试并确认它们在旧实现上失败。

### Task 2：实现 DOCX 确定性后处理

**文件：**
- 修改 `backend/exporter.py`

1. 增加东亚字体和语言 XML 工具函数。
2. 对默认样式、正文/表格/页眉/页脚运行应用字体元数据。
3. 对表格设置重复表头、行不可拆分和可跨页布局。
4. Pandoc 生成后调用后处理并原位保存。
5. 运行专项测试。

### Task 3：实现 PDF 针对性分页

**文件：**
- 修改 `frontend/scripts/render_pdf.mjs`

1. 渲染前对长表和技术标识单元格加确定性 class。
2. 短表保持整体保护；长表仅在行边界跨页。
3. 保留表头重复、页眉页脚和原字号行距。
4. 运行专项测试并检查真实 Shaw 候选报告。

### Task 4：真实离线渲染与一致性验证

**私有产物：**
- `private-paper-review-runs/.../p5-b2-20261110/`

1. 保留修复前 PDF/DOCX 和联系表。
2. 用分支代码导出修复后 Markdown、DOCX、PDF。
3. 生成 PDF 及 LibreOffice 辅助渲染截图和前后联系表。
4. 检查中文、公式、Issue/Evidence ID、页眉页脚、长短表及末页内容。
5. 明确 Word/WPS 是否可用；不可用则标记未验证。

### Task 5：完整回归、文档与 PR

**文件：**
- 修改 `docs/p5-b2-export-quality.md`（如不存在则新建）

1. 运行 P4 专项和完整后端测试。
2. 运行前端生产构建及相关浏览器回归。
3. 执行敏感信息扫描和 Git diff 自审。
4. 提交并推送 `feature/p5b2-export-quality`。
5. 创建唯一的 B2.1 PR，等待 CI 全部结束。
6. 不合并、不启动 B2.2、不执行真实模型调用。
