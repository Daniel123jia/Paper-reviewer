# P3.1 三篇公开论文记录响应重放与人工初审

本记录使用 P2-B 私有运行数据库中冻结的完整 P1 Source Bundle、P2-A/B 结果、P2-C 原始完整结果及三份实际 PDF。P3.1 用首轮保存的脱敏模型响应重放服务端判定，没有发起新的模型请求；表中调用次数、Token 与模型运行耗时是首轮实测数据，不代表重放消耗。原始 Schema 1.0 输出保存在各论文的 `history/p3-v1.0/`，原始评测见[历史版本](history/p3-real-evaluation-v1.0.md)。公开目录只保留结构化结果、来源映射和脱敏调用元数据；完整模型请求与响应、PDF 和渲染区域仍在 Git 忽略的私有目录。以下“人工初审”是工程侧逐问题核对，`human_review_status` 仍为 pending，等待 ChatGPT 独立学术验收。

| 论文 | P2-C 任务/P3 判定 | 收窄 | 不确定 | 调用 | 输入/输出 Token | 重试/未知 Token | 累计耗时 | 工具记录 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Shaw 2018 | 6/6 | 1 | 5 | 23 | 356,527 / 25,889 | 0 / 0 | 12m 41s | 125 |
| DistilBERT 2019 | 9/9 | 1 | 8 | 32 | 450,144 / 36,571 | 0 / 0 | 19m 12s | 15 |
| fastText 2016 | 9/9 | 2 | 7 | 31 | 427,671 / 32,282 | 0 / 0 | 16m 46s | 8 |

三篇的运行状态均为 limited，继承了上游来源局限；24 个任务全部有判定，没有 issue 级 `concern_supported` 或 `concern_refuted`。这反映了当前表格视觉、外部代码和缺失性断言门槛，不能解读为论文没有可讨论的问题。若某一子检查被反证，其状态为 contradicted，原始 Issue 仍保留完整谱系。详见各目录的 [Shaw 结果](examples/p3-shaw2018/evidence_verification_result.json)、[DistilBERT 结果](examples/p3-distilbert2019/evidence_verification_result.json)、[fastText 结果](examples/p3-fasttext2016/evidence_verification_result.json)。各目录还提供原始 `p2c_consolidated_issues.json`、`p2c_verification_tasks.json`、`issue_adjudications.json` 和 `run_summary.json`。

相对首轮规则，Shaw 第 5、6 项，DistilBERT 第 5、6 项及 fastText 第 7 项从 `concern_narrowed` 降为 `inconclusive`，因为已定位材料不足以可靠支持相应子问题或修订后的问法。DistilBERT 第 2 项仍为 `concern_narrowed`，但因最终问法依赖尚未可审计视觉确认的 Table 2 行列对应关系，`report_eligibility` 从 `eligible_with_qualification` 降为 `requires_human_review`。DistilBERT 第 5 项、fastText 第 7 项的自动报告资格亦因判定降级而转为人工复核；其他任务资格不变。历史判定和本次重放结果均保留，不能将重放称为再次进行真实模型审稿。

## Shaw 2018：6 项逐问题初审

编号与 JSON 中的 P2-C/P3 数组顺序一致。页码为 PDF 可见页，详细 Evidence ID、坐标和正反向引用见完整 JSON。

| # | 原始疑点与 P3 判定 | 原文与反证人工核对 | 未完成的核验 |
| --- | --- | --- | --- |
| 1 | 基线与相对位置配置可比性；inconclusive | PDF 第 4 页 Table 1 分 base/big、absolute/relative 四行；同页明确说作者自行生成 baseline，并列训练、优化和解码设置。不能仅因 base 与 big 参数不同认定同组比较不公平。 | 单次运行差异、完整命令和配置文件未核查；不作为已证实混杂因素。 |
| 2 | 7% 吞吐下降与实际效率范围；inconclusive | PDF 第 3 页给出 7% steps/s 下降、同型号 P100 与批量设置，并解释张量重排和共享相对表示。它不是实测峰值内存或全场景效率证明。 | 计时边界、原始速度、显存和长度扩展测量未独立取得。 |
| 3 | 存储复杂度范围；inconclusive | 第 3 页 3.3 节讨论的是相对位置表示存储及 self-attention 空间，不是整套 Transformer 的总内存。作者明确写出跨 head 共享和具体渐近项。宽泛指控已被大幅削弱。 | 运行期其它张量/缓冲区及实际内存尚未核验，当前结果不可直接写成“作者复杂度错误”。 |
| 4 | 未见长度泛化；concern_narrowed | 第 2–3 页将此表述为假设及裁剪标签的表示可定义性；第 4 页 Table 2 是裁剪距离消融，并非独立的训练长度外推实验。 | 保留的仅是“泛化指表示可用还是预测性能”的限定提问；不能指控算法错误或断言全文没有实验。 |
| 5 | BLEU 评测管线；inconclusive（原为 concern_narrowed） | 第 4 页给出 newstest2014/newstest2013 的不同用途、word-piece、beam 4、长度惩罚 0.6；不能声称评测设置完全缺失。 | 已定位的材料不能充分支持原模型所写的收窄问题；历史 tensor2tensor 版本、BLEU 脚本及 token/大小写处理未独立查证。 |
| 6 | 联合使用 sinusoidal 与 relative 的对照；inconclusive（原为 concern_narrowed） | 第 4 页正文明确说测试过联合使用且未观察到收益；Table 1 可见四个单独 absolute/relative 条件，没有联合条件数值。原“没有做实验”被反证。 | 当前定位记录不足以把“未给出匹配配置/重复次数”的收窄表述自动放行；保留原疑点、原文和子检查待人工核对。 |

## DistilBERT 2019：9 项逐问题初审

| # | 原始疑点与 P3 判定 | 原文与反证人工核对 | 未完成的核验 |
| --- | --- | --- | --- |
| 1 | 三重目标实际实现；inconclusive | 第 2 页解释三项损失、温度、初始化与动态 masking；论文也指向公开代码。不能说目标完全未说明。 | 具体损失权重、token 选择和代码版本未检查；外部代码不可用时受限。 |
| 2 | SQuAD 数值差异；concern_narrowed，`requires_human_review`（原为 `eligible_with_qualification`） | **人工查看原始 PDF 第 3 页 Table 2**：DistilBERT (D) 一行是 79.1 EM / 86.9 F1，普通 DistilBERT 是 77.7/85.8。**第 4 页连续正文**写两阶段蒸馏得到 70.4 EM / 79.8 F1。两处数值确实不同，Table 2 的列与行视觉上可辨。 | 两段是否使用完全同一 split、checkpoint 和评估条件尚未确认；只能要求作者澄清，不能断言表格错误。自动结构化记录仍标 `visual_check_required`，本文的人工查看尚未成为可审计的视觉确认记录，因此阻断自动报告。 |
| 3 | 预训练成本比较范围；inconclusive | 第 3 页报告学生在 16GB V100 上约 90 小时，并与 RoBERTa 的另一配置比较；另有教师依赖。 | 教师成本分摊、同配置基线与外部训练脚本未核查；不能把 90 小时直接等同完整端到端成本。 |
| 4 | 单组件消融能否证明联合信号；inconclusive | 第 4 页 Table 4 确实逐个移除损失或改变初始化，作者已有真实消融。 | 是否存在匹配的 MLM-only 双损失同时移除对照及完整训练条件未核实；不能说“没有消融”。 |
| 5 | 97% 保留性能的范围；inconclusive（原为 concern_narrowed） | 第 3 页 Table 1 可见 GLUE Score 79.5（BERT-base）与 77.0（DistilBERT），77.0/79.5≈96.9%；正文写明 macro-score，表中还有各任务指标。 | 数值和聚合口径需视觉确认；当前子检查与定位依据不足以自动采纳模型生成的收窄问题。 |
| 6 | 预训练与任务特定蒸馏优势；inconclusive（原为 concern_narrowed） | 第 4 页 related work 的措辞有比较含义，但 SQuAD 段落实际上使用两个阶段；Table 4 只比较损失/初始化。 | 不同阶段的比较条件及外部先前工作未核验；保留原疑点待人工核对。 |
| 7 | CPU/移动速度边界；inconclusive | 第 3 页 Table 3 可见 BERT-base 668 秒、DistilBERT 410 秒；以处理速度表达约 63% 提升，不能把“60% faster”机械解释为耗时下降 60%。第 4 页给出 CPU 型号与 batch 1。 | 框架、精度、线程及手机工作负载详情未独立查证；不可宣称计算错误。 |
| 8 | 实验配置可复现性；inconclusive | 论文提供架构、初始化、评估聚合、数据和部分资源，并说明代码/权重发布。 | 优化器日程、完整预处理与脚本版本未检查；不把当前上下文缺口认作全文缺失。 |
| 9 | 蒸馏收益归因；inconclusive | 与第 4、6 项有重叠：Table 4 支持单组件和初始化效应，SQuAD 是连续两阶段结果。 | MLM-only 联合对照和 task-specific-only 匹配比较仍不明确。P4 前应处理跨 Issue 重复，但本阶段保持 P2-C 全部任务。 |

## fastText 2016：9 项逐问题初审

| # | 原始疑点与 P3 判定 | 原文与反证人工核对 | 未完成的核验 |
| --- | --- | --- | --- |
| 1 | 单 epoch 与达成精度的总训练速度；concern_narrowed | 原始 PDF 第 3 页 Table 2 标题明确为**单个 epoch**；同页结果段写 fastText 跑 5 epoch，并说明 CNN 用 GPU、fastText 用单线程 CPU，作者承认实现优化可能影响对照。 | 保留的只是完整训练时间及配置口径澄清，不能指控原表本身错误。 |
| 2 | YFCC100M 过滤和切分复现；inconclusive | 第 4 页给出词频阈值、训练/验证/测试数量，且承诺发布重建脚本。 | 脚本与原始 ID 未取得；不能说作者未提供任何复现路径。 |
| 3 | Huffman 复杂度假设；inconclusive | 第 2 页使用 Huffman 路径并给出复杂度文字；频率加权平均可以在适用分布下解释该界。 | 构树频率、平均口径和采样分布未形式化确认，不能说数学结论已被推翻。真实运行没有可安全执行的标量公式核验。 |
| 4 | 与 averaged word2vec 的对比；concern_narrowed | 结论的对比与监督分类实验相关；论文把 word2vec 视作无监督特征背景，并不报告完整匹配的外部方法比较。 | 收窄为结论措辞及外部文献依据；外部论文未实际核查，不能判优先权或 SOTA。 |
| 5 | top-T heap 与总搜索工作；inconclusive | 方法段将部分预测复杂度表述为经验观察，YFCC 大标签实验确有速度数据。 | 没有独立节点访问计数或理论最坏情形证明；不能把单个 heap 更新复杂度当成总预测复杂度。 |
| 6 | 计时边界与训练预算；inconclusive | 第 3–4 页已披露多项 epoch、硬件类别、线程和部分模型设置，单 epoch 与总时间并非完全混淆。 | CPU 具体型号、计时操作范围和基线停止准则未复核。 |
| 7 | 算法贡献与实现/并行收益；inconclusive（原为 concern_narrowed） | 论文明确讨论实现收益和层次化计算，tag 预测对比也声明线程条件；笼统地说作者没有区分来源不成立。 | 缺少能够直接支持原模型收窄问题的匹配消融定位；保留原疑点待人工核对。 |
| 8 | 先过滤再切分是否泄漏；inconclusive | 第 4 页叙述顺序确实先说过滤再说切分，但没有证明频率计算使用了测试标签来训练，也没有说明明确的随机种子。 | 必须拿到重建脚本或处理日志；**绝不可称“已证实数据泄漏”**。 |
| 9 | 多标签如何转成单标签目标；inconclusive | 第 2 页模型目标、第 4 页多标签样本和 prec@1 同时存在；文本不能唯一还原目标转换/权重。 | 训练脚本和完整数据协议未检查；不把提取不足认作作者错误。 |

## 工具、撤回和风险

实际运行产生 Shaw 125、DistilBERT 15、fastText 8 条工具记录。主要是指标字面值定位、严格标签比较、PDF 区域提取和外部材料不可用标记；Shaw 的多组数值产生大量 `not_comparable`，没有被写成数值冲突。DistilBERT 的 70.4/79.8 与 Table 2 数值没有被程序强行当成相同实验配置。三篇均没有真实标量公式验证结果，也没有独立实验复现或外部文献核验。未验证的 PDF 表格和公式继续保留视觉复核状态。

本轮没有整项 `concern_refuted`，也没有“确认作者错误”的结果。被撤回的是若干**原始子断言**：Shaw “没有联合位置实验”、DistilBERT “没有组件消融”、fastText “未披露单 epoch 或硬件条件”等；对应 Issue 保留更窄或不确定的问题。所有判定的原始 Candidate ID、正反向 Evidence、任务状态和工具 ID 见完整 JSON。

质量风险仍有：自动文字定位不能确认表格行列；选中上下文不能证明全文缺失；多个 DistilBERT 问题可能重复；代码/数据/外部文献未实际核验；模型子检查解释可能包含不正确的工具可用性叙述，现已显式标为模型解释且不进入服务端最终判定理由。三篇均需 ChatGPT 继续检查科学判断。ChatGPT 网页端深度审稿参考结果尚未提供，质量对标状态 **pending**；不得据此声称达到同等水平。
