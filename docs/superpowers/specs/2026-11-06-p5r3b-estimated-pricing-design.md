# P5-R3B 暂估价格能力探测设计

## 目标

在不改变 P5-R2 默认严格价格门槛的前提下，为 P5-R3B 的一次性非论文模型能力探测增加显式的暂估价格模式。本改动只扩展探测工具、专项测试和说明文档，不修改 P1–P4、冻结 Manifest、Prompt、论文或评测基线。

## 命令行契约

新增单一布尔开关 `--allow-estimated-pricing`。

- 不传入开关时，保持当前严格模式：单价、HTTPS 价格来源、`provider_source_checked` 确认值和有效核验日期缺一不可。
- 传入开关时，可以在输入、输出单价为有限非负数时通过价格前置检查，不要求价格来源 URL、核验日期或人工核验标记。
- 暂估模式报告固定写入 `pricing.status=estimated_unverified`、`source_evidence_status=not_provided_or_not_used`、`independently_verified=false` 和 `account_hard_limit=false`。报告不写入价格来源 URL，不伪造核验日期或 `provider_source_checked`。
- 报告标记适用范围为 `p5r3b_single_nonpaper_capability_probe`，并明确排除 P1–P4 正式工作流、完整 P5 评测和发布验收。
- 开关只改变价格准入，不改变调用授权。真实请求仍必须同时传入 `--execute` 和精确确认串 `P5R2_MINIMAL_PROBE_AUTHORIZED`。

## 价格与费用语义

暂定计划单价由操作者显式配置：

- `OPENAI_INPUT_USD_PER_MILLION=0.80`
- `OPENAI_OUTPUT_USD_PER_MILLION=4.00`

按 128 输入 Token 规划估算和 64 输出 Token 请求值计算，单次规划费用为 `$0.0003584`。该数值不是账户费用硬上限，128 也不是可在请求前强制的输入 Token 上限。

Probe Schema 从 `1.2` 升级到 `1.3`。配置报告继续保留 `estimated_probe_cost_usd` 作为规划估算。执行报告将本地根据供应商返回 usage 计算的数值与供应商实际扣费分开：

- 保留 `actual_cost_usd` 供 1.2 读取方兼容，其语义明确为“按实测 usage 和操作者单价计算的本地值”。
- 新增 `provider_billed_cost_usd=null` 和 `is_provider_billed_cost=false`，避免将本地计算值误报为账户实际扣费。
- 严格模式保留 `operator_attested_rate_calculation`；暂估模式使用 `estimated_unverified_rate_calculation`。

## 环境文件与覆盖规则

`--environment-file` 可以指向现有本地 `.env.local`，但本次开发和测试不指向真实文件，不读取真实密钥值。后续获得真实调用授权后，探测器可从该路径读取密钥仅用于 SDK 认证，不将值打印、写入报告、复制到新文件或提交到 Git。

环境合并改为“环境文件先读取，当前进程环境后覆盖”。这使命令层显式设置的遥星 HTTPS 端点、冻结模型和暂估单价不会被旧文件静默覆盖。如果两个来源对同一变量给出不同非空值，报告只记录冲突变量名和“process_environment_wins”策略，不记录任何值。冲突本身不阻断，因为最终值的权威来源已被明确定义并可审计。

## 数据流与失败关闭

1. CLI 在读取 Manifest 和构造客户端前验证报告输出路径并写入预请求失败报告。
2. 冻结 Manifest 必须继续要求 `gpt-6.1-sol`。
3. 环境合并产生有效配置和脱敏冲突元数据。
4. 价格解析根据 CLI 开关进入严格或暂估分支。价格缺失或非法时，两个模式均在客户端构造前失败。
5. 即使暂估价格配置可用，缺失 `--execute` 或精确授权串也不会构造客户端。
6. 真实请求形状保持不变：固定非论文短文本、最多一次请求、SDK 自动重试为 0、`max_tokens=64`、180 秒超时、`stream=True` 和 `stream_options.include_usage=True`。不回退模型，不改参重试，不启动论文工作流。

## 测试策略

所有新增测试使用临时环境文件和模拟客户端，绝不访问遥星或任何模型接口。覆盖：

- 不传开关时，未核验价格继续阻断。
- 传入开关且单价有效时，零网络配置报告为 `estimated_unverified`。
- 暂估模式价格缺失或非法仍然阻断。
- 仅有暂估开关而无授权串时，客户端不得构造。
- 暂估模式报告不含供应商已核验表述、价格 URL、密钥、端点值或原始异常消息。
- 环境文件与进程环境冲突时，进程值生效且报告只列出冲突变量名。
- 请求次数、重试、Token 参数、流式 usage、模型和 Manifest 限制保持不变。
- CLI 失败路径返回原有非零退出码，并保留脱敏失败报告。
- 旧严格模式和零网络检查在 Schema 1.3 下继续通过。

## 交付边界

开发在 `feature/p5r3b-estimated-pricing-probe` 分支完成，使用测试先行。验证包括 P5-R2 专项测试、完整后端回归和相关确定性验收，再提交独立 PR。本阶段不读取真实密钥值、不执行 `--execute`、不合并 PR，不调用模型也不启动任何论文审稿。P5 保持 **FAIL**，ChatGPT 对标保持 **pending**，P5-R3B 新增真实模型调用保持 0。
