# 公开上传验证记录

日期：2026-11-04（Asia/Shanghai）

本记录对应首次公开代码审查上传。测试过程没有启用 `RUN_REAL_AI`、`RUN_REAL_REVIEW` 或 `RUN_REAL_DEEP`，没有调用真实模型。

## 环境

- Python 3.11.15
- Node.js 22.21.1
- npm 10.9.4
- Vite 7.3.7

## 后端

```text
命令：python -m pytest backend/tests -q
使用本地公开论文 PDF：47 passed, 6 warnings
干净仓库不附 PDF：45 passed, 2 skipped, 6 warnings
```

干净仓库跳过的两个用例需要 `REAL_PDF_PATH`，分别是真实 PDF 的算法核验和实验数值核验。

## 前端构建

```text
命令：npm ci && npm run build
结果：通过，319 modules transformed
警告：主 JavaScript chunk 大于 500 kB
```

`npm ci` 报告 4 个 low severity 依赖告警。本次为保留现有依赖和业务实现，没有执行自动升级或 `npm audit fix --force`。

## Playwright

```text
命令：REAL_PDF_PATH=<local-public-paper.pdf> npm run test:e2e
结果：5 passed, 7 skipped, 1 failed
```

跳过项需要真实 AI、访问保护、已存报告或用量数据的显式环境开关。

未通过用例：

```text
frontend/tests/real-pdf.spec.ts
opens an existing paper for a fresh browser without a saved selection
```

失败现象：新建浏览器上下文后，未找到预期的已上传论文按钮。该失败在上传前和最终待提交副本中都可复现。按照本次任务边界，未修改前端业务逻辑或测试期望。

## 安全和许可边界

- 未提交 `.env.local`、API Key、访问口令、PDF、SQLite、导出报告、运行日志或模型原始请求/响应。
- 诊断论文只保留 ACL Anthology 公开链接和不含原文的元数据 fixture。
- 待提交文件中未检出本地私人绝对路径或常见密钥格式。
- 远端仓库在本次上传前为空，没有旧 Git 历史需要清理。
