# P5-R2 模型连接与能力探测说明

## 状态与边界

P5-R2 只检查模型配置并准备一次最低成本能力探测。默认命令不创建模型客户端、不连接网络、不消耗 Token。当前没有授权执行真实请求，也没有授权启动 Shaw、SARDet-100K 或 SAM 审稿。P5 产品发布验收仍为 **FAIL**，ChatGPT 网页端质量对标仍为 **pending**。

## 当前模型调用架构

- 后端统一使用 `openai>=2,<3` 的 `OpenAI` 客户端和 OpenAI-compatible Chat Completions API，没有第二套供应商客户端。
- P2-A、P2-B、P2-C、P3 使用流式 `chat.completions.create`，请求 `stream_options={"include_usage": true}`，超时 180 秒，SDK 自动重试为 0。
- P4 使用同一 SDK 的非流式 Chat Completions；连接或超时错误最多再尝试一次。P4 的 Token 来自非流式响应 `usage`。
- P2-A 至 P3 的“结构化输出”是 Prompt 要求 JSON，随后由本地 JSON/Pydantic 契约校验；生产请求目前没有使用供应商的 `response_format` 或 JSON Schema 强制模式。
- 请求模型来自 `OPENAI_MODEL`；各阶段分别记录实际返回模型、输入/输出 Token、耗时、状态和错误类型。若供应商不返回 usage，系统保留未知用量，不应按零成本处理。
- `backend/app.py` 导入时读取仓库根目录 `.env.local`，且文件值覆盖同名旧进程变量。应用启动会初始化本地数据结构，但模型请求只在明确创建理解、审查、核验、聊天或报告任务后发生；启动本身不会调用模型。

代码在未设置 `OPENAI_BASE_URL` 时会回退到 `https://www.yaoxing.best/v1`。这只能证明它是当前硬编码的 OpenAI-compatible 中转端点，不能证明服务主体、模型能力或计价。为了冻结评测可审计，P5-R2 探测拒绝使用隐式回退，要求显式配置实际端点。

## 本地配置

在本分支工作树根目录创建 `.env.local`，不要把密钥发送到聊天或写入前端：

```dotenv
OPENAI_API_KEY=你的实际密钥
OPENAI_BASE_URL=服务商提供的真实 HTTPS /v1 地址
OPENAI_MODEL=gpt-6.1-sol
OPENAI_INPUT_USD_PER_MILLION=服务商确认的每百万输入Token美元价格
OPENAI_OUTPUT_USD_PER_MILLION=服务商确认的每百万输出Token美元价格
OPENAI_PRICING_SOURCE_URL=服务商HTTPS价格页
OPENAI_PRICING_VERIFIED_AT=人工核验日期YYYY-MM-DD（不得晚于当天，最多30个自然日）
OPENAI_PRICING_VERIFICATION=provider_source_checked
```

执行 `chmod 600 .env.local`。`.gitignore` 已忽略 `.env.local`、`.env` 和 `.env.*`（仅保留 `.env.example`），因此正常 Git 操作不会提交该文件。后端已经运行时必须重启，因为 `.env.local` 在 Python 模块加载时读取。不要把旧工作树的密钥文件自动复制到新评测工作树；应由操作者确认端点、模型和价格后单独配置。

只进行零费用配置检查：

```zsh
python -m scripts.p5r2.capability_probe \
  --environment-file .env.local \
  --manifest docs/examples/p5-regression/evaluation-manifest.json \
  --output /tmp/p5r2-configuration.json
```

该报告只包含 `configured/not_configured`、端点是否有效、模型是否匹配、价格及其来源是否由操作者核验、以及阻断项；不会写入密钥、原始端点、价格来源 URL 或端点主机名。单价字段只代表操作者输入；只有同时提供 HTTPS 来源、精确人工确认标记和有效日期时才满足探测前置条件。日期晚于运行当天会以 `provider_pricing_verification_future_dated` 阻断；超过 30 个自然日会以 `provider_pricing_verification_expired` 阻断，必须由操作者重新核验供应商价格。报告仍明确标记为“操作者证明、未经程序独立核验”。

### P5-R3B 暂估价格配置检查

严格价格核验仍是默认行为。只有操作者在单次命令中显式加入 `--allow-estimated-pricing`，探测器才允许用未经供应商核验的单价完成配置准备。该模式只适用于 `p5r3b_single_nonpaper_capability_probe`，不适用于 P1–P4 正式论文工作流、完整 P5 评测或发布验收。

可以保留旧的本地密钥文件原位不动，由 `--environment-file` 指向它，同时在当前进程显式指定端点、冻结模型和暂估单价。以下命令只做零网络配置检查，不包含 `--execute`：

```zsh
OPENAI_BASE_URL=https://www.yaoxing.best/v1 \
OPENAI_MODEL=gpt-6.1-sol \
OPENAI_INPUT_USD_PER_MILLION=0.80 \
OPENAI_OUTPUT_USD_PER_MILLION=4.00 \
python -m scripts.p5r2.capability_probe \
  --environment-file /existing/private/project/.env.local \
  --manifest docs/examples/p5-regression/evaluation-manifest.json \
  --output /tmp/p5r3b-configuration.json \
  --allow-estimated-pricing
```

对于这个独立探测脚本，当前进程环境的优先级高于 `--environment-file`。如果两个来源的非空值不同，报告仅记录冲突的变量名和 `process_environment_over_environment_file` 策略，不保存任何值、文件路径或端点主机名。这项优先级只在 `scripts/p5r2/capability_probe.py` 内实现，不改变 P1–P4 的生产环境加载顺序。

按当前操作者暂定的输入 `$0.80/百万 Token` 和输出 `$4.00/百万 Token`，128 输入 Token 规划估算加 64 输出 Token 请求值为 `$0.0003584`。报告固定标记 `pricing.status=estimated_unverified`、`independently_verified=false` 和 `account_hard_limit=false`；不伪造价格来源 URL、核验日期或 `provider_source_checked`。该金额只是规划估算，不是供应商账户的实际扣费或严格费用上限。

`--allow-estimated-pricing` 绝不构成真实调用授权。即使价格配置通过，发出请求仍必须同时加入 `--execute` 和精确授权串 `P5R2_MINIMAL_PROBE_AUTHORIZED`。

## 当前实际配置状态（2026-11-06）

- P5-R2 工作树没有 `.env.local`。
- 启动本次检查的进程没有 `OPENAI_API_KEY`、`OPENAI_BASE_URL`、`OPENAI_MODEL` 或价格变量。
- 旧的非 Git 工作目录存在一个只含 `OPENAI_API_KEY` 变量名的本地文件，但其值未读取、未输出，也不会被本工作树自动加载。
- 冻结 Manifest 请求模型为 `gpt-6.1-sol`；代码默认值与其相同，但受控探测要求 `OPENAI_MODEL=gpt-6.1-sol` 显式配置。
- API 服务来源、凭证有效性、实际返回模型、Token usage 和计费仍未通过真实请求验证。

## 最低成本能力探测

探测固定为一条非论文请求：要求返回 `{"ok":true,"probe":"p5r2"}`。它复用 P2-A 至 P3 的流式 Chat Completions 形状，限制如下：

- 最多 1 次调用；SDK 自动重试 0。
- 固定短输入；128 Token 只是规划估算，客户端无法在请求前精确强制输入 Token 上限。实际输入 usage 超过估算时探测失败并记录 `input_token_estimate_exceeded`。
- 请求发送 `max_tokens=64`。这是供应商参数而不是本地截断；兼容性需要真实响应验证。供应商拒绝该参数会记录请求失败，返回 usage 超过 64 时记录 `output_token_limit_exceeded` 并使探测失败。
- 超时 180 秒。
- 请求 `include_usage`，记录实际输入、输出和总 Token。
- Usage 的三个字段必须都是非负原生整数，并且总 Token 必须等于输入与输出 Token 之和；缺失标为 `incomplete`，负数、非整数或矛盾统计标为 `invalid`，两者均不得计费或通过探测。
- 记录请求模型、响应模型、JSON 解析结果、耗时和脱敏错误类型/HTTP 状态。
- 响应模型不等于冻结模型、usage 缺失或 JSON 无法通过契约时均失败关闭。

真实执行还需要同时满足：有效配置、操作者确认的两项单价、`--execute` 和精确授权确认串。当前任务不执行以下命令；只有用户另行明确授权后才可运行：

```zsh
python -m scripts.p5r2.capability_probe \
  --environment-file .env.local \
  --manifest docs/examples/p5-regression/evaluation-manifest.json \
  --output /tmp/p5r2-probe.json \
  --execute \
  --authorization-ack P5R2_MINIMAL_PROBE_AUTHORIZED
```

规划费用估算公式为：

```text
(128 × 输入单价 + 64 × 输出单价) / 1,000,000 美元
```

这里的 128 不是硬限制，因此上述数值不是严格费用上限。价格必须来自实际 API 服务商可核验的账单或价格页面；仓库不推断中转服务的价格。只有单价而没有来源、核验日期和人工确认时，状态为 `operator_configured_unverified`，真实探测保持阻断。满足这些字段后状态为 `operator_attested_provider_pricing`，仍不声称程序独立验证了价格，也不代表供应商账户设置了费用硬限额。

CLI 退出码用于自动化失败关闭：输出路径和 Manifest 有效时，配置检查返回 0 并在报告中列出环境阻断项；输出路径或 Manifest 无效时返回 2。真实探测成功返回 0；请求完成但模型、JSON、usage 或预算验证失败返回 1；授权或配置在请求前失败返回 2。所有具备可写输出路径的真实探测失败路径都会先保存脱敏报告。

探测报告 Schema `1.3` 保留 1.2 报告读取方使用的旧字段。`actual_cost_usd` 仍为按供应商返回 usage 和操作者单价计算的本地兼容字段，新增 `provider_billed_cost_usd=null` 和 `is_provider_billed_cost=false` 以明确它不是供应商账户实际扣费。CLI 会在读取 Manifest 和构造模型客户端之前创建输出目录并写入 `probe_not_started` 报告，以确认正式请求的审计路径可用。Manifest 缺失、JSON 无效、`requested_model` 类型错误或不等于冻结模型时返回 2，并用稳定阻断码覆盖该报告；不会保存 Manifest 原文或异常消息。若报告路径本身不可写，CLI 返回 2 且绝不构造客户端，但无法在该路径保存报告。

## 静态检查与真实请求的验证边界

无需调用即可确认：环境文件路径和覆盖顺序、冻结模型字符串、使用的 SDK/API 形状、流式 usage 请求、超时和 SDK 重试、Token/耗时记录字段、默认不联网、报告脱敏和显式授权门。

必须真实请求才能确认：网络连通、凭证有效、`gpt-6.1-sol` 是否可用、供应商实际返回的模型标识、流式 JSON 内容、usage 是否完整以及实际耗时。

一次最低成本探测仍不能确认：P4 非流式响应、长上下文窗口、论文级输出上限、多调用稳定性、失败恢复、P2-A 至 P4 全链路兼容性和科学审稿质量。探测成功后应先报告实测 Token、耗时、实际模型和基于已核验价格的费用，再另行申请完整 Shaw 运行预算；不得自动开始论文审稿。

## 预算与剩余风险

- 当前 P2-A/P2-B/P2-C/P3 分别有调用、输入 Token、输出 Token 和耗时预算；冻结 P5 Manifest 的每篇论文上限为 90 次调用、1,200,000 输入 Token、220,000 输出 Token、10,800 秒和 2 次重试。
- 冻结 Manifest 的 `max_cost_usd` 仍为空，价格来源仍标记未知；配置价格变量只能计算/记录费用，不能替代供应商侧账户硬限额。
- P4 没有与 P2-A 至 P3 同等的整份运行 Token/费用硬预算，且现有非流式生产调用没有显式输出 Token 上限。这是完整真实运行前必须单独评估的风险，本阶段不修改 P4。
- 数据库保存论文 Source Bundle、结构化模型结果和调用元数据；不会保存 API Key。论文原文和本地数据库必须继续留在受限目录，不得提交公共仓库。
- 当前错误审计主要保存异常类型而非原始供应商错误正文，降低了密钥/端点泄露风险；真实探测报告也只保存脱敏异常类型和状态码。

## 隐式默认端点静态审计

`backend.app.model_base_url()` 在缺少 `OPENAI_BASE_URL` 时返回默认中转地址。当前所有下列真实模型路径都会通过该函数取得地址：P2-A 论文理解、P2-B 专业审查、P2-C 候选整合、P3 证据核验、旧版审稿与深化复核、论文对话、旧版英文草稿，以及 P4 模型工厂。因此，在未显式配置端点的生产进程中触发这些功能，论文或证据内容可能发送到未经本次评测确认的默认中转服务。

P5-R2 探测已经失败关闭：没有显式 HTTPS `OPENAI_BASE_URL` 就不会构造客户端。完整 P5-R 运行也必须先通过这一检查。生产层的最小后续安全修改是取消 `model_base_url()` 的隐式默认值，或为所有付费入口统一增加显式端点预检；这会改变 P1–P4 既有运行配置和部分测试，属于冻结基线变更，必须另行设计、授权和验收，本 PR 不实施。

## P4 费用风险与最小后续方案

P4 的 `write_report()` 会先生成写作结果，再调用一致性检查；校验失败最多再进行一轮。底层连接/超时又允许一次重试。作者报告把全部组作为一个批次，内部/编辑报告按问题组分别处理，因此实际供应商尝试次数会随组数增加。当前 P4 没有整份草稿的调用、已知 Token、耗时或费用硬预算，也没有显式输出 Token 参数；冻结 Manifest 的 `max_cost_usd` 仍为空。

不修改当前 P4 的前提下，完整真实运行继续保持阻断。后续最小变更方案应独立实施：

1. 在创建草稿前建立每份草稿的最大供应商尝试次数、已知输入/输出 Token 和耗时预算，并把未知 usage 视为不完整而不是零成本。
2. 为每次 P4 调用加入经当前供应商验证的输出 Token 参数；参数不兼容时失败关闭，不静默去掉限制。
3. 用实际问题组数量计算最坏调用次数，并在调用前拒绝超过剩余预算的草稿。
4. 只有价格来源完成独立人工核验并设置非空运行费用上限后，才允许冻结 P5 真实运行；代码估算不能代替供应商账户侧消费限额。
5. 保留现有 `p4_model_calls` 的请求模型、实际模型、Token、耗时、状态和脱敏错误记录，不覆盖 P3/P4 科学判定。
