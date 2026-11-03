# P2 本地验收记录（2026-11-03）

## 已运行

- 中转站 `https://www.yaoxing.best/v1`，模型 `gpt-6.1-sol`，使用后端私有 `.env.local` 的 API key，通过 OpenAI SDK Chat Completions 流式接口获得真实回答。未把密钥写进前端或文档。
- 真正上传并解析本机 5 页论文 `Self-Attention with Relative Position Representations.pdf`；浏览器连续提问两轮，收到逐段回答。第二轮回答包含 Markdown 表格。
- 两轮回答各有两个可解析的 Evidence ID。点击引用后跳到原始 PDF 对应页并高亮；刷新页面后恢复四条历史消息（两条用户、两条助手）。
- 对同一 PDF 按 SHA-256 复用解析结果；本轮模型请求只发送选中的最多六个文本证据块和最近八条消息，不发送整份 PDF。
- 最新通过的真实浏览器用例对应两次已完成模型调用：第一轮输入 5369 Token、输出 74 Token、耗时 5394 ms；第二轮输入 5507 Token、输出 162 Token、耗时 6682 ms。两次的 `cost_usd` 均为未知，因为尚无该中转站的可信计价配置。
- 主动中断网络连接的浏览器用例显示错误，也不会留下未持久化的假回答。

## 验证命令与结果

- `.venv/bin/python -m pytest backend/tests -q`：12 passed。
- `npm run build`：通过 TypeScript 检查并生成 Vite 产物。
- `PLAYWRIGHT_BASE_URL=http://127.0.0.1:5174 RUN_REAL_AI=1 npx playwright test --timeout=120000`：3 passed，1 skipped，包括真实中转站双轮调用、真实 PDF 阅读检索、连接失败恢复。缺少密钥用例在当前已配置密钥的环境下跳过，其后端 503 行为由单元测试覆盖。

## 遗留问题

- 后续补充真实模型的公式问答验收：论文第 2 页公式 (4) 以独立 LaTeX 公式显示，行内 `\\(...\\)` 也已正确渲染；引用跳转到该公式原文。模型回复只能说明原文，不能替代数学验证。
- 当前证据检索按字词匹配；跨语言问答、同义词与扫描 PDF 需要后续增强。Evidence ID 只定位原文，技术结论仍需 P3/P4 独立核查。
- 任务状态目前只有回答中的状态；详细任务进度、审稿结论、DOCX/PDF 导出和服务器部署属于后续阶段。
- 前端构建存在 PDF.js 和主包体积警告，不影响本次本地验收，后续可按实际加载性能拆包。
