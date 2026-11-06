# P5-R 真实模型续跑准备记录

## 范围与冻结基线

P5-R 从 `main` Commit `09c0ef8e0210d5973d4d9d5e82a4ff90a560831c` 创建独立分支 `feature/p5r-fresh-model-validation`。本次只建立真实模型续跑的可审计准备层，不修改 P1–P4 核心算法、Prompt、P3 判定阈值、P4 准入规则、冻结论文或独立参考问题。

确定性预检工具位于 `scripts/p5r/preflight.py`。它只读取 Manifest、指定 PDF、盲测参考集和本地环境标记，不连接模型 API。公开预检结果见 `docs/examples/p5r/preflight.json`。P5-R.1 将预检 Schema 升级为 `1.1`。

## P5-R.1 失败关闭规则

| 项目 | P5-R 初版 | P5-R.1 |
|---|---|---|
| Git 基线 | 只记录 `baseline_is_ancestor` | 不是已验收 main 基线的后代时立即拒绝 |
| 冻结 Manifest | 读取其 Commit 字段 | `baseline_commit` 和 `code_commit` 必须同时等于已验收的 P4 冻结 Commit |
| 模型配置 | 环境值直接写入记录 | 环境 `requested_model` 必须与 Manifest 一致，否则需新的授权评测版本 |
| Prompt | 未在公开结果中展开 | 校验实际 Prompt 文件的路径、版本与 SHA-256，并保留完整清单 |
| 盲测参考 | 校验 paper ID 和 PDF 哈希 | 额外强制 `frozen=true`、`viewed=false`、固定 Issue ID 集合及整文件 SHA-256 |
| 预检来源 | `current_commit` 可能被误解为包含当前未提交修改 | 显式记录 `committed_source_validation` 或 `pre_commit_worktree_validation` 及 dirty 状态 |

## 冻结材料校验

| 论文 | Canonical paper ID | 版本 | SHA-256 | 结果 |
|---|---|---|---|---|
| Shaw 2018 | `shaw2018` | arXiv 1803.02155 / NAACL 2018 | `8086e2e5e139fd576631ce0ec4560c5ee66225bec260d631a3e8fe7f1f13d50b` | 通过 |
| SARDet-100K | `sardet100k-neurips2024` | NeurIPS 2024 final proceedings | `3f4e6cddbf32524853f3493a5e7975490b8ffeae274cf139acf2b576736998b9` | 通过 |
| SAM | `sam-iclr2021` | arXiv 2010.01412v3 / ICLR 2021 | `73ace22a61c52152fc84a94239cb3ea5f9fa9e753aa8ef5995f277365e25b764` | 通过 |

SARDet 和 SAM 的 `BlindReferenceSet` 分别与 Manifest 中的 canonical ID 及 PDF 哈希一致，各包含 6 项冻结参考问题。它们还必须保持 `frozen_before_etpaper_output=true` 和 `etpaper_output_viewed=false`。P5-R.1 固定了两份文件的 Issue ID 顺序和整文件 SHA-256，因此不能以数量相同的新问题覆盖原参考集。它们的 provenance 是 `non_expert_source_grounded_reference`，不是完整专家金标准，后续只能报告对这些枚举问题的覆盖情况。

PDF 只存在被 Git 忽略的 `data/p5r/sources/` 中，不提交到公开仓库。预检严格要求 canonical ID；历史别名仅用于读取旧记录，不能作为新运行标识。

## 模型环境预检

| 项目 | 当前结果 |
|---|---|
| P5-R 工作树 `.env.local` | 不存在 |
| 当前进程 `OPENAI_API_KEY` | `not_configured` |
| 端点标记 | `default_application_endpoint` |
| requested model | `gpt-6.1-sol` |
| actual model | `unknown` |
| 后端模型请求超时 | 180 秒 |
| SDK 自动重试 | 0 |
| API 网络、流式输出、JSON 解析、Token 用量 | 未测试，等待真实调用授权 |

requested model 只是配置值，不代表服务端实际返回的模型。必须等最低成本能力探测成功后，才能记录 `actual_model`、provider 返回用量和能力兼容性。当前没有发起网络请求，也没有将旧调用或 Mock 当作新结果。

凭证必须使用仓库根目录下被 `.gitignore` 排除的 `.env.local` 或启动进程的受控环境变量配置；不得写入前端、日志、截图或公开文档。

## 调用、Token、时间与费用预算

历史成功路径在 P4 写作之前约为：P2-A 11–13 次、P2-B 6 次、P2-C 4–5 次、P3 23–32 次，即约 44–56 次模型调用、640k–740k 已知输入 Token，再加 P4 中英文写作与一致性检查。P4 调用数取决于整合问题组数，因此当前只能给出约 55–85 次的运行前估计，不能承诺固定值。任何失败且用量未知的调用仍占用调用名额，不计为零成本。

| 范围 | 最大调用 | 已知输入 Token | 已知输出 Token | 墙钟 | 显式重试 |
|---|---:|---:|---:|---:|---:|
| 每篇 | 90 | 1,200,000 | 220,000 | 10,800 秒 | 2 |
| 三篇理论上限 | 270 | 3,600,000 | 660,000 | 32,400 秒 | 6 |

当前未配置可验证的服务商单价，因此美元成本为 `unknown`。不根据模型名或自定义端点自行猜测价格。如运营者在本地配置 `OPENAI_INPUT_USD_PER_MILLION` 和 `OPENAI_OUTPUT_USD_PER_MILLION`，预检可按冻结 Token 上限给出明确标注为“运营者配置”的估算。

## 授权门槛与顺序

当前 `ready_for_paid_run=false`，阻断项为：

1. P5-R 运行环境未配置凭证。
2. 尚未执行经用户授权的最低成本能力探测。
3. `actual_model` 及用量返回能力未知。

下一步不是启动全论文审稿，而是在凭证安全配置并获得明确授权后，执行一次最小能力探测。探测通过后仍需再次确认高成本运行预算，然后严格按 `Shaw 2018 → SARDet-100K → SAM` 串行。Shaw 出现环境或执行器故障时必须停止，不得直接消耗后两篇论文的预算。

## 确定性回归结果

| 检查 | 结果 |
|---|---|
| P5-R 预检专项 | 16 passed |
| 完整后端回归 | 276 passed, 2 skipped |
| 前端生产构建 | passed |
| PDF 阅读、公式、旧审稿模式与失败处理浏览器回归 | 6/6 passed |
| P4 编辑、定位和下载浏览器回归 | 1/1 passed |

本地系统没有全局 Pandoc，回归使用项目 Python 环境已锁定的 pypandoc 捆绑二进制文件；这与 CI 显式安装 Pandoc 的前置条件等价。安装锁定的前端依赖后，所有 PDF 导出回归通过；未为本机环境差异修改 P4 代码。

## 隐私与保留原则

- 公开仓库只保留脱敏环境状态、哈希、运行元数据和必要的公开论文证据摘要。
- API Key、端点主机、完整模型请求/响应、PDF、SQLite、渲染页面和运行日志只能位于 Git 忽略且限制访问的本地目录。
- 本轮使用的三篇均为公开论文，但仍不在 Git 中提交 PDF 原文。
- ChatGPT 网页端对标继续为 `pending`，不自行生成参考意见。
