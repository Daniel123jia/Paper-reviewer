# 复现指南

## 环境

- Python 3.11
- Node.js 20 或更高版本
- npm
- Pandoc（DOCX/PDF 导出需要）
- Playwright Chromium（浏览器测试和 PDF 导出需要）

本次上传前使用 Python 3.11.15、Node.js 22.21.1 和 npm 10.9.4 进行验证。

## 安装

```bash
git clone https://github.com/Daniel123jia/Paper-reviewer.git
cd Paper-reviewer
python3.11 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
cd frontend
npm ci
npx playwright install chromium
cd ..
```

## 环境变量

```bash
cp .env.example .env.local
```

编辑 `.env.local`。最小真实 AI 配置为：

```dotenv
OPENAI_API_KEY=replace-with-your-api-key
OPENAI_BASE_URL=https://api.example.com/v1
OPENAI_MODEL=replace-with-your-model-name
```

- 端点必须兼容 OpenAI Chat Completions API。
- 当前 `backend/app.py` 在未设置环境变量时保留了原实现的默认中转站 URL 和 `gpt-6.1-sol` 模型名。该站点的可用性、账号和密钥不属于仓库，第三方应显式设置 `OPENAI_BASE_URL` 和 `OPENAI_MODEL`。
- 未设置 `OPENAI_API_KEY` 时，PDF、检索、离线核验和中文模板仍可使用；对话、AI 审稿、Deep Challenge 和英文翻译不可用。
- 未配置费率时费用记录为未知。
- `.env.local` 被忽略，不要提交。

## SQLite 初始化

不需要单独迁移命令。首次导入 `backend.app` 或启动 Uvicorn 时，`create_app()` 在 `PAPER_REVIEW_DATA_DIR` 中创建 `review.db` 和 `files/`。默认目录是项目根目录下的 `data/`。

## 启动

终端 1：

```bash
.venv/bin/python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

终端 2：

```bash
cd frontend
npm run dev
```

访问 `http://127.0.0.1:5173`。Vite 将 `/api` 代理到 `http://127.0.0.1:8000`，可用 `VITE_API_TARGET` 覆盖。

## 公开测试论文

诊断所用论文：Peter Shaw, Jakob Uszkoreit, Ashish Vaswani, *Self-Attention with Relative Position Representations*, NAACL 2018。

- ACL Anthology：<https://aclanthology.org/N18-2074/>
- DOI：<https://doi.org/10.18653/v1/N18-2074>

仓库不重新分发 PDF。请从公开来源下载，并在本机使用。历史解析结构、选中 ID 和脱敏状态样例见 `tests/fixtures/shaw2018-review-snapshot.json`。

## 运行一次审稿

1. 在首页上传测试 PDF。
2. 打开“审稿分析”。
3. 选择“一审”并创建任务。
4. 点击“开始 AI 分析”。当前工作流会选择证据、调用 `propose`、调用 `challenge`，并保存 findings。
5. 如需要，对单个 finding 启动 Deep Challenge。
6. 公式、BLEU 和算法维度核验由各自界面按钮独立触发。
7. 打开报告页生成中文内部报告或正式意见。英文草稿需要模型翻译调用。

当前代码不保存完整模型 Prompt 和原始响应，因此无法从脱敏 fixture 恢复历史原始输出。

## 导出

- Markdown：不需要额外系统工具。
- DOCX：需要 Pandoc。
- PDF：需要 Pandoc、Node.js、已安装的 Playwright Chromium。

在报告编辑器中选择对应格式下载。

## 测试

后端：

```bash
.venv/bin/python -m pytest backend/tests -q
```

前端构建：

```bash
cd frontend
npm run build
```

Playwright 需要已启动的前后端以及本地 PDF：

```bash
cd frontend
REAL_PDF_PATH=/absolute/path/to/N18-2074.pdf npm run test:e2e
```

部分用例还需要显式开关：`RUN_REAL_AI`、`RUN_REAL_REVIEW`、`RUN_REAL_DEEP`、`RUN_REAL_EXPORT`、`RUN_AUTH_E2E`、`RUN_USAGE_E2E` 或 `TEST_MISSING_KEY`。真实 AI 开关会消耗 Token，默认关闭。

## 无法仅依赖仓库复现的内容

- 历史真实模型响应：当时未保存原始请求和响应。
- 原本的精确 Evidence ID 前缀：来自随机 snapshot UUID。
- 历史费用：未配置可靠单价的调用记录为未知。
- 原机的私有中转站密钥、访问口令、SQLite、日志、PDF 和导出报告：出于安全和数据边界不上传。
