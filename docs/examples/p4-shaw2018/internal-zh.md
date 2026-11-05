# 待审核草稿 / DRAFT

此版本尚未完成审稿人最终审核。

# 内部完整审稿报告

## 报告范围

本报告按 6 个审查主题保留 6 项 P3 核验问题，其中 1 项具备限定性自动草稿资格。以下意见保留原始判定与待核验事项，仅供审稿人内部审阅，不代表已确认的论文错误。

## 需要澄清的问题

### 1. 澄清位置表示对照实验的配对条件与结果来源

关于实验设置及表格结果对应的绝对位置与相对位置比较，请进一步明确：在每个模型规模和翻译方向内，配对模型是否采用一致的训练、模型选择、解码和评估条件，从而支持将所报告的 BLEU 差异归因于位置表示。作者已明确说明重新生成正弦绝对位置基线，并给出适用于各实验的词表、批处理、优化和解码设置；模型配置描述中的条件性措辞也支持这些通用设置适用于两种位置表示。因此，现有文字为共享实验条件提供了较充分的支持。不过，逐次运行是否遵循所述训练与检查点策略、是否存在额外的开发集选择或停止规则，以及具体 BLEU 评分和输出处理流程，仍需说明。建议提供简要的配对配置说明或已有运行记录，确认各对照在训练更新次数、dropout、硬件分配及检查点选择或平均策略上的一致性，并明确表中绝对位置结果与作者重新生成基线的对应关系。另请说明随机初始化和数据顺序的处理方式；如已有独立重复运行结果，可报告其变异，以帮助判断较小增益的稳健性；如没有，可明确说明这一限制。这些澄清有助于区分位置表示的贡献与训练、选择或评分流程可能带来的影响。

原文位置：PDF page 3; PDF page 4.

建议作者回应：补充按模型规模和翻译方向组织的配对配置说明，确认绝对位置与相对位置模型的训练、检查点选择及平均、解码与评分条件；说明任何额外的开发集调参或停止决策，给出 BLEU 计算及文本处理设置，并明确表中基线的运行来源。说明初始化和数据顺序的处理方式，报告已有重复运行的变异，或注明尚无此类结果。可通过现有配置、日志或文字澄清完成回应，无需默认新增实验。

- P3: inconclusive; report_eligibility: requires_human_review; human_review_status: pending; reviewer_approval_status: pending
- 未完成检查：Do run configurations confirm that each absolute/relative pair followed the stated configuration-specific update count, dropout, hardware allocation, and checkpoint policy, including the last-20-checkpoint averaging procedure for big models?; How were random initialization and data ordering controlled, and were the gains reproduced across multiple seeds? Identical seeds are not themselves a prerequisite for a valid comparison, but variability matters when assessing the smaller gains.; Were there any additional checkpoint-selection, development-set tuning, or stopping decisions beyond the stated training durations and averaging rules?

### 2. 澄清相对位置实现的效率比较基线与测量范围

在实现效率及实验配置的相关段落（所提供材料定位于 PDF 第 3–4 页）中，作者说明通过分解注意力计算和张量重排实现相对位置计算，并报告在 P100 GPU 上保持模型与批量大小时，每秒训练步数下降 7%。作者还说明，为隔离位置表示的影响，重新生成了采用正弦位置编码的 Transformer 基线。这些说明为实现可行性和比较设计提供了依据，但目前尚不清楚该计时结果是否直接使用这一重新生成的基线，以及它对应的数据集、模型配置与计时范围。由于每秒训练步数受到序列长度、批量构造、软硬件配置及计时边界的共同影响，这些信息决定了该结果能够支持何种范围的实际效率结论。请明确比较对象、测量配置和完整训练步的定义，说明计时是否包含前向与反向计算、优化器更新、设备通信、输入处理、检查点保存及评估，并交代预热、计时窗口和重复测量方式。也请说明比较时软件版本、数值精度、设备数量、序列长度分布、填充与批量构造等条件是否一致。对于内存与扩展性，请区分文中存储复杂度讨论的计入范围与实际峰值内存，并说明现有运行时间和内存结果能否支持不同序列长度、批量大小及表示共享设置下的结论；若已有相关测量，请给出位置或汇总，若尚未测量，则明确当前效率结论的适用范围即可。

原文位置：PDF page 3; PDF page 4.

建议作者回应：补充效率结果的基线身份、数据集与模型配置、计时边界及测量流程，说明关键执行条件是否匹配；界定存储复杂度所计入的张量及其与实际内存占用的关系，并引用已有的绝对步时、峰值内存或扩展性测量。若没有这些测量，请明确效率结论仅适用于已报告的配置和指标，无需默认开展新的实验。

- P3: inconclusive; report_eligibility: requires_human_review; human_review_status: pending; reviewer_approval_status: pending
- 未完成检查：Was the 7% decrease measured against the authors' regenerated sinusoidal-position Transformer baseline, a result from Vaswani et al. (2017), or another implementation? Which dataset and model configuration produced it?; What did the steps-per-second timing include: complete forward and backward computation, optimizer updates, device communication, input processing, checkpoint saving, and evaluation? What warmup, timing window, and repetition protocol were used?; Were software versions, numerical precision, device count and utilization, sequence-length distributions, padding, batch construction, and checkpoint behavior matched for the timing comparison?

### 3. 澄清空间复杂度分析是否仅涵盖表示存储

第 3.3 节空间复杂度分析的统计范围需要进一步明确。现有摘录中，作者首先明确讨论了通过跨注意力头及序列共享来减少相对位置表示的存储，随后却使用“整体自注意力空间复杂度”描述所得空间界。需要核对原文版面及相邻文字是否已明确说明，后者仍仅指表示张量的存储。作者关于计算分解与张量重排的说明有助于理解如何避免广播相对位置表示，但仅凭这些说明尚不能确定整体空间界包含哪些中间张量。该定义直接影响读者将此结果理解为表示存储开销，还是完整自注意力计算的内存需求。请指出原文中已有的范围限定；若尚未明确，建议在该空间界附近补充说明其包含与排除的张量，以及跨头、跨序列共享的适用条件。若该表述确实意指完整自注意力内存需求，请说明成对得分及归一化权重的存储如何计入。

原文位置：PDF page 2; PDF page 3; PDF page 4.

建议作者回应：请核对第 3.3 节原文及相邻文字，指出“整体自注意力空间复杂度”是否已有仅针对表示存储的明确限定；如无，请补充统计范围和共享假设。若该空间界涵盖完整自注意力计算，请进一步说明成对得分、归一化权重等中间张量的存储处理及其如何计入该界。

- P3: inconclusive; report_eligibility: requires_human_review; human_review_status: pending; reviewer_approval_status: pending
- 未完成检查：Does the original Section 3.3 layout or additional surrounding text explicitly qualify 'overall self-attention space complexity' as representation storage, beyond the opening sentence's narrower restriction?; Are complete pairwise compatibility and normalized-weight tensors materialized, and which tensors are retained, overwritten, or recomputed during forward computation and training? Computing all pairwise values does not alone establish their simultaneous retention.; For the base model's unique representations per head, does the implementation materialize O(hn²da) relative-representation tensors shared across sequences, or use another storage strategy? How is its memory bound distinguished from the shared-head bound?

### 4. 澄清未见序列长度泛化的含义与证据范围

在介绍相对位置裁剪的段落中，作者指出该设计“enables the model to generalize to sequence lengths not seen during training”。请明确，这里的泛化是指相对位置表示在训练中未见的长度下仍然适用，还是指模型在这些长度上仍能保持翻译性能。作者关于相对位置不依赖序列总长度、裁剪后使用固定关系标签集合的解释，为前一种含义提供了结构上的依据。不过，所提供的裁剪距离消融报告了不同裁剪设置下的开发集整体 BLEU，未单独识别训练长度范围之外的表现，因此尚不足以说明后一种含义。区分这两种主张，有助于读者准确理解方法的适用范围及实验结论。如果作者仅指表示机制的适用性，建议在该处明确限定表述；如果也主张训练长度范围外的翻译性能能够保持，请指出相应的训练长度边界及超出该边界的评估结果，并说明长度定义、评估样本的划分方式，以及与正弦位置编码基线比较时的模型配置和长度限制是否一致。

原文位置：PDF page 2; PDF page 3; PDF page 4; PDF page 5.

建议作者回应：请明确“泛化到训练中未见的序列长度”的含义。若仅指表示机制仍然适用，请相应限定原文措辞；若包含性能保持的主张，请定位已有支持证据，交代训练长度边界、长度的定义及边界外的分长度评估，并说明相关基线比较条件。若尚无此类证据，可将该表述限定为结构上的适用性或待验证的性能假设。

- P3: concern_narrowed; report_eligibility: eligible_with_qualification; human_review_status: pending; reviewer_approval_status: pending
- 未完成检查：Does 'enables the model to generalize' mean representational applicability at unseen lengths, or does it also claim maintained translation performance?; Does the complete paper specify maximum training sequence lengths or measured source and target length distributions, beyond the supplied batch token budgets?; Does the complete paper evaluate sequences outside the training-length range, with results separated by sequence length or length bucket?

### 5. 请明确 BLEU 评分与词片还原流程

针对实验设置、工具库脚注及结果部分，请明确 BLEU 评分实现、大小写处理、分词、参考译文处理以及解码后词片还原的具体流程。作者已说明使用 tensor2tensor 进行训练和评估，提供了工具库链接、词片词表与解码设置，并区分了主要结果和模型变体实验使用的评估集；作者还说明自行生成基线结果，以隔离工具库与实验配置变化的影响。这些说明有助于理解比较设计，但所提供的摘录尚不能唯一确定最终评分所用的实现与处理选项。上述细节关系到结果能否复现，以及基线与相对位置模型的分数是否在一致条件下获得。请在实验设置中补充相应说明，或指出正文、脚注及补充材料中已有说明的位置，并确认每项基线与相对位置模型比较是否采用相同的评分流程和参考译文。

原文位置：PDF page 3; PDF page 4.

建议作者回应：请提供 BLEU 实现及其版本和选项，说明大小写与评分分词设置、解码词片还原及空格和标点处理方式，以及各语言对和评估集使用的参考译文及其预处理方式。可通过可定位的 tensor2tensor 修订版本、任务定义、配置和评估命令说明这些选择，并确认各项基线与相对位置模型比较中的评分处理和参考译文是否一致；若相关信息已载于正文、脚注或补充材料，请给出具体位置。

- P3: inconclusive; report_eligibility: requires_human_review; human_review_status: pending; reviewer_approval_status: pending
- 未完成检查：Which BLEU implementation, version, and options produced the reported scores, including tokenization and case handling?; How were decoded word pieces reconstructed into scoring inputs, including whitespace and punctuation processing?; Which reference files and reference preprocessing were used for each language pair and evaluation set?

### 6. 澄清相对位置与正弦位置编码联合条件的结果位置及比较范围

作者在摘要和机器翻译结果讨论中指出，在相对位置表示之外加入正弦位置编码未带来进一步收益，结果讨论同时指向表 1。请明确：完整的表 1、其他表格或补充材料中，何处列出了联合使用相对位置表示与正弦位置编码的条件及其 BLEU 结果？目前可用的表格提取内容仅能辨识绝对位置表示和相对位置表示各自单独使用的条件，尚不足以判断完整表格是否列有联合条件。明确这一结果的出处，有助于读者判断“未带来进一步收益”的适用范围。建议作者指出相应表格行或材料位置，并说明联合条件覆盖的模型规模与语言方向，以及其训练、检查点处理和解码设置是否与对应的单独使用条件一致。若现有材料未单列这些结果，可补充已有实验的分数，并说明运行次数及判断“无进一步收益”的依据；如已有重复运行，也请说明结果的变动情况。

原文位置：PDF page 1; PDF page 3; PDF page 4; Table 2, PDF page 4; Table 3, PDF page 5.

建议作者回应：请标明联合条件结果所在的表格行或补充材料位置；若尚未单列，请补充已有实验结果。请同时说明其模型规模、语言方向、与对应单独使用条件的设置是否一致，以及运行次数和“无进一步收益”的判断依据。

- P3: inconclusive; report_eligibility: requires_human_review; human_review_status: pending; reviewer_approval_status: pending
- 未完成检查：Does the complete Table 1, another table, or accompanying material identify a combined relative-plus-sinusoidal condition and its BLEU scores? The supplied extraction does not resolve this.; Which model sizes and language pairs were evaluated with combined representations, and did each comparison use the same architecture, dropout, relative-position settings, training update budget, checkpoint policy, and decoding settings as its corresponding single-representation conditions?; Was the no-benefit observation based on a single run or repeated runs, and are individual scores, variability estimates, or a criterion for what counted as a benefit available?

## 核验限制与来源

- consolidated_9825c343099d3753e676: inconclusive / requires_human_review; Were each absolute-position baseline and its corresponding relative-position model trained, selected, decoded, and evaluated under matched conditions so that the reported BLEU differences can be attributed to the position representations?
  - 待检查：Do run configurations confirm that each absolute/relative pair followed the stated configuration-specific update count, dropout, hardware allocation, and checkpoint policy, including the last-20-checkpoint averaging procedure for big models?; How were random initialization and data ordering controlled, and were the gains reproduced across multiple seeds? Identical seeds are not themselves a prerequisite for a valid comparison, but variability matters when assessing the smaller gains.; Were there any additional checkpoint-selection, development-set tuning, or stopping decisions beyond the stated training durations and averaging rules?
- consolidated_105a91210515bf8f5b82: inconclusive / requires_human_review; What baseline and measurement scope underlie the reported 7% decrease in steps per second, and do matched runtime, memory, and scaling measurements substantiate the claimed practical efficiency of the relative-position implementation?
  - 待检查：Was the 7% decrease measured against the authors' regenerated sinusoidal-position Transformer baseline, a result from Vaswani et al. (2017), or another implementation? Which dataset and model configuration produced it?; What did the steps-per-second timing include: complete forward and backward computation, optimizer updates, device communication, input processing, checkpoint saving, and evaluation? What warmup, timing window, and repetition protocol were used?; Were software versions, numerical precision, device count and utilization, sequence-length distributions, padding, batch construction, and checkpoint behavior matched for the timing comparison?
- consolidated_fe5b94a19e9e06327e40: inconclusive / requires_human_review; Check the original Section 3.3 layout and surrounding text for an explicit restriction of the space analysis to representation storage.
  - 待检查：Does the original Section 3.3 layout or additional surrounding text explicitly qualify 'overall self-attention space complexity' as representation storage, beyond the opening sentence's narrower restriction?; Are complete pairwise compatibility and normalized-weight tensors materialized, and which tensors are retained, overwritten, or recomputed during forward computation and training? Computing all pairwise values does not alone establish their simultaneous retention.; For the base model's unique representations per head, does the implementation materialize O(hn²da) relative-representation tensors shared across sequences, or use another storage strategy? How is its memory bound distinguished from the shared-head bound?
- consolidated_e3eac49834cebb136759: concern_narrowed / eligible_with_qualification; Could the authors clarify whether 'enables the model to generalize to sequence lengths not seen during training' denotes representational applicability or maintained translation performance? The located clipping ablation does not isolate unseen-length performance; if the latter meaning is intended, what training-length boundary and evaluation beyond that boundary support it?
  - 待检查：Does 'enables the model to generalize' mean representational applicability at unseen lengths, or does it also claim maintained translation performance?; Does the complete paper specify maximum training sequence lengths or measured source and target length distributions, beyond the supplied batch token budgets?; Does the complete paper evaluate sequences outside the training-length range, with results separated by sequence length or length bucket?
- consolidated_c5155e1e615fb4a2358a: inconclusive / requires_human_review; Inspect the complete experimental setup, footnotes, and any supplementary material for the BLEU scorer, case handling, tokenization, reference processing, and word-piece reconstruction procedure.
  - 待检查：Which BLEU implementation, version, and options produced the reported scores, including tokenization and case handling?; How were decoded word pieces reconstructed into scoring inputs, including whitespace and punctuation processing?; Which reference files and reference preprocessing were used for each language pair and evaluation set?
- consolidated_3a9cf1ce576dc3d2beda: inconclusive / requires_human_review; Does the full Table 1 or another table contain a combined relative-plus-sinusoidal condition?
  - 待检查：Does the complete Table 1, another table, or accompanying material identify a combined relative-plus-sinusoidal condition and its BLEU scores? The supplied extraction does not resolve this.; Which model sizes and language pairs were evaluated with combined representations, and did each comparison use the same architecture, dropout, relative-position settings, training update budget, checkpoint policy, and decoding settings as its corresponding single-representation conditions?; Was the no-benefit observation based on a single run or repeated runs, and are individual scores, variability estimates, or a criterion for what counted as a benefit available?
- 37 项解析覆盖疑点保留在冻结来源中，需按相关原文逐项复核。
- 5 项表格结构限制保留在冻结来源中。
- 2 项图像或图注限制保留在冻结来源中。
- The supplied quality state flags ambiguous reading order, incomplete section mapping, partial source mapping, and missing human-readable document and page references.
- All five listed formula objects on pages 2–3 remain unverified. Fragmented extractions do not establish complete mathematical transcriptions, including the attention equations, edge representation notation, relative position indexing, clipping definitions, dimensional specifications, and implementation transformations.
- Source quality: reading_order_ambiguous
- Source quality: section_mapping_incomplete
- Source quality: source_mapping_partial
- Source quality: human_reading_reference_missing
- Source quality: human_page_reference_missing
- formula formula_ev_886cadb1a708_1_33: detected_unverified
