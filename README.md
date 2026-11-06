# AI 论文深度审稿工作台

P0–P2 已验收。真实 PDF 可上传、阅读、检索并跳转高亮证据；论文对话支持中转站流式回答。P3 已实现审稿任务与抽样候选问题筛查，测试结果和限制见 `docs/phase3-acceptance.md`。P4 的核心本地验收已通过：自动关联明确引用的公式、离线标量推导与反例、算法矩阵维度核查、实验数值与结论核对、扩展原文反证；支持范围和遗留问题见 `docs/phase4-acceptance.md`。P5 已实现可编辑的中英文完整报告与正式意见，支持 Markdown、Word、PDF 导出；本地验收与限制见 `docs/phase5-acceptance.md`。P6 的本地集成、单账号保护、恢复、并发及用量检查见 `docs/phase6-acceptance.md`。尚未部署到服务器。

P1 结构化解析诊断已加入只读的 Source Bundle 与解析质量 API。它保留现有 Evidence ID 和审稿 Baseline，不改变模型 Prompt 或报告流程；结构候选及未可靠解析的内容会明确标记。接口契约与限制见 `docs/p1-source-bundle-schema.md`，三种解析器的隔离对照见 `docs/p1-parser-comparison.md`。

P1.1 增加候选 `reading_units[]`、章节映射和按任务范围判断的输入质量门槛；这些结果仍需原始 PDF 参照，不能自动当作可靠全文。实际验收见 `docs/p1.1-reading-acceptance.md`。

## 本地运行

需要 Python 3.11、Node.js 20 以上，以及 Pandoc。PDF 导出使用 Playwright Chromium，首次安装前端依赖后执行 `cd frontend && npx playwright install chromium`。

```bash
python3.11 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
cd frontend && npm ci
```

在项目根目录运行后端：

```bash
.venv/bin/python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

另开终端运行前端：

```bash
cd frontend && npm run dev
```

访问 `http://127.0.0.1:5173`。上传的原件和 SQLite 数据保存在项目的 `data/` 私有目录。可用 `PAPER_REVIEW_DATA_DIR` 指定其他数据目录。请勿直接把无鉴权的本地开发服务暴露到公网。

## Mac 终端配置 AI API key

推荐在项目根目录用 Vim 编辑 `.env.local`。该文件被 `.gitignore` 忽略，后端启动时读取，文件中的值优先于终端旧环境变量：

```zsh
cd /path/to/Paper-reviewer
cp .env.example .env.local
vim .env.local
```

在 Vim 中按 `i`，输入以下内容（密钥用你自己的值）：

```dotenv
OPENAI_API_KEY=你的实际密钥
OPENAI_BASE_URL=https://api.example.com/v1
OPENAI_MODEL=你的模型名称
```

再按 `Esc`，输入 `:wq` 并回车保存。执行 `chmod 600 .env.local` 后重启后端。不要在聊天中发送密钥。当前中转站地址和模型也是代码默认值；`OPENAI_BASE_URL` 和 `OPENAI_MODEL` 可覆盖它们。

也可以只在启动后端的**同一个 zsh 终端窗口**临时设置：

```zsh
cd /path/to/Paper-reviewer
read -s "OPENAI_API_KEY?请粘贴 API Key：" && export OPENAI_API_KEY && echo
.venv/bin/python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

临时输入时终端不回显。密钥仅交给后端进程，不要放入前端代码。若后端已启动，先按 `Ctrl+C` 停止，再运行启动命令。若要计算费用，按中转站的实际计价设置 `OPENAI_INPUT_USD_PER_MILLION` 和 `OPENAI_OUTPUT_USD_PER_MILLION`；未设置时费用记为未知，Token 与耗时仍会记录。

## 单账号访问保护

本地默认只监听 `127.0.0.1`，未配置访问口令时不显示登录页。若要在本地验证登录，可把一个**独立于 AI API key**、随机生成且至少 32 字符的口令放入 `.env.local`：

```dotenv
PAPER_REVIEW_ACCESS_TOKEN=在本机生成的随机访问口令
```

重启后端后，浏览器会显示登录页。会话 Cookie 为 HttpOnly、SameSite Strict，有效期 12 小时；退出登录会清除浏览器 Cookie。预部署检查模式还需设置 `PAPER_REVIEW_PREDEPLOY=1` 和 `PAPER_REVIEW_SECURE_COOKIE=1`，后者必须配合 HTTPS。不要把访问口令或 AI API key 写进前端代码。当前没有配置公网 TLS、反向代理或登录限速，不能把本地开发服务直接开放到公网。

页面右上角的“模型用量”汇总聊天、审稿与英文草稿的 Token、执行时间和已知费用。未配置单价的调用显示为“费用未知”，不会误显示为零成本。

### P5-R2 零费用模型配置检查

冻结真实评测要求显式设置 `OPENAI_BASE_URL`、`OPENAI_MODEL=gpt-6.1-sol`、由实际服务商确认的输入/输出单价，以及服务商价格来源、最近 30 个自然日内且不晚于当天的核验日期和人工确认标记。仅填写单价不能启动真实探测。可先运行以下命令；它默认不创建模型客户端、不连接网络：

```zsh
python -m scripts.p5r2.capability_probe \
  --environment-file .env.local \
  --manifest docs/examples/p5-regression/evaluation-manifest.json \
  --output /tmp/p5r2-configuration.json
```

真实能力探测有独立的显式授权门，本项目不会因为检测到 API Key 就自动调用模型。架构、费用公式及尚未验证的能力见 [P5-R2 模型连接与能力探测说明](docs/p5r2-model-capability-probe.md)。

## 验证

```bash
.venv/bin/python -m pytest backend/tests -q
cd frontend && npm run build && npm run test:e2e
```

浏览器测试使用 `REAL_PDF_PATH` 指定本机的公开测试论文路径；仓库不附带论文 PDF。运行浏览器测试时需要先启动前后端。详见 `docs/reproduction-guide.md`。
P5 真实审稿任务的文档编辑与下载测试可设置 `RUN_REAL_EXPORT=1`；该测试需要已存在的真实论文任务，其具体记录见 `docs/phase5-acceptance.md`。
P6 可使用 `RUN_AUTH_E2E=1`、`RUN_USAGE_E2E=1` 分别运行受保护服务及用量页面的浏览器测试。真实 PDF 的本地烟测脚本为 `scripts/p6_smoke.py`，需指定 `--pdf` 和 `--base`；受保护服务通过同一终端的 `PAPER_REVIEW_ACCESS_TOKEN` 环境变量登录，不会调用模型。
