# P0 与 P1 阶段验收记录

日期：2026-11-03。

## 已运行功能

FastAPI 接收并校验 PDF，保存原件和 SHA-256，使用 PyMuPDF 解析页面文字块与章节，写入 SQLite。相同 SHA-256 复用解析结果。API 提供文档列表、原件、证据详情和全文文字块检索。检索结果尽量使用 `search_for` 返回的命中矩形。React 页面上传文件、检索、跳转章节和证据，PDF.js 渲染原始页面并在缩放后更新高亮。无文字页面标记需要 OCR。

## 验证结果

- `.venv/bin/python -m pytest backend/tests -q`：5 通过。覆盖真实 PDF 字节往返、非 PDF 拒绝、重复上传、章节识别和无文字页面。
- `npm run build`：通过 TypeScript 检查与 Vite 构建。构建提示主包大于 500 kB。
- `npm audit --audit-level=high`：0 项漏洞。
- `npm run test:e2e`：1 通过。真实五页论文《Self-Attention with Relative Position Representations》在 Chromium 中完成上传、搜索、证据高亮、缩放、翻页和章节跳转。截图在 `frontend/test-results/real-pdf-evidence.png`。
- 中文真实论文《低空监视技术及其发展趋势综述》通过后端临时数据目录测试。21 页，解析出 7 个章节，搜索“低空监视技术”返回 18 个文字块结果，首条在第 1 页。

## 边界和遗留问题

- 章节识别使用 PDF 书签优先，其次匹配常见标题和编号。复杂版面或未编号标题可能漏检。
- 检索按文字块做大小写不敏感的字串匹配，最多返回 100 项。跨块短语和 OCR 页面不可检索。
- 高亮已在英文双栏论文中验收。旋转页、特殊裁剪页和复杂公式区域尚未用真实样例核对。
- 当前是本地开发服务，尚无账号鉴权。部署服务器前必须在 P6 完成访问控制。
- PDF.js 构建产物较大，后续需按页面懒加载以优化首次加载。
- 测试输出含 PyMuPDF 类型弃用提示和 FastAPI 测试客户端依赖提示，测试本身通过。
