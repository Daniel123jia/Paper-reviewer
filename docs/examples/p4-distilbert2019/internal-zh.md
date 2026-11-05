# 待审核草稿 / DRAFT

此版本尚未完成审稿人最终审核。

# 内部完整审稿报告

## 报告范围

本报告按 8 个审查主题保留 9 项 P3 核验问题，其中 0 项具备限定性自动草稿资格。以下意见保留原始判定与待核验事项，仅供审稿人内部审阅，不代表已确认的论文错误。

## 需要澄清的问题

### 1. 澄清联合训练目标及消融实验的实现定义

关于训练损失、温度、学生初始化及表中损失消融的描述，需要明确的科学问题是：原文与对应实验的代码及配置，能否唯一确定作者实际使用的联合训练目标及消融条件？作者已说明教师软目标监督、掩码语言建模和隐藏状态余弦对齐的组合，指出师生训练时使用相同温度，并介绍了动态掩码、从教师交替层初始化学生以及训练代码和权重的发布途径。这些信息为理解方法提供了基础，但当前所供材料尚不足以确定各损失的权重、训练温度及温度相关缩放、参与计算的词元位置、隐藏状态配对方式和损失归约规则。初始化时的层选择也不能直接确定余弦损失所比较的层。上述定义会影响各项损失的实际相对贡献，因此也关系到如何解释移除某项损失后的性能变化。请作者在训练目标与消融实验的相关位置补充这些定义，或给出与所报告模型及消融结果直接对应的版本化代码和配置入口，并说明移除损失项后其余系数与归约规则如何处理。

原文位置：PDF page 2; PDF page 3; PDF page 4; Table 4, PDF page 4.

建议作者回应：请提供与所报告预训练模型及损失消融实验对应的完整目标函数定义或版本化实现与配置，明确：各损失的数值系数；训练温度及是否采用温度相关缩放；各项损失对掩码、未掩码、特殊及填充词元的处理；师生是否接收相同的动态掩码输入；余弦对齐所用的师生层及隐藏状态；各项损失在词表类别、隐藏维度、词元、样本及梯度累积批次上的归约和归一化方式；移除某项损失时，剩余系数是否保持不变或重新归一化，以及归约规则是否变化。若这些信息已在发布材料中给出，请标明对应文件、版本和实验配置。

- P3: inconclusive; report_eligibility: requires_human_review; human_review_status: pending; reviewer_approval_status: pending
- 未完成检查：What numerical coefficients weight distillation, masked language modeling, and cosine alignment in the reported pretraining run?; What numerical training temperature was used, and was the distillation loss multiplied by a temperature-dependent factor such as T squared?; Which token positions contribute to each loss, including treatment of padding, special tokens, masked positions, and unmasked positions?

### 2. 澄清适配阶段蒸馏的结果及完整模型比较基线

在适配阶段蒸馏的正文讨论中，作者报告了70.4 EM和79.8 F1，并称其与完整模型相差不超过3分。表2的提取文本则将DistilBERT（D）与79.1 EM和86.9 F1联系起来；这一行列及指标对应关系仍需核对原表。作者已说明该阶段以经过SQuAD微调的BERT作为教师，表注也将D定义为微调期间增加一次蒸馏，并将问答评估标为SQuAD 1.1开发集上的EM/F1。这些说明有助于理解方法，但尚不足以确认正文与表格对应相同的模型检查点和评估配置。请作者核对上述数值及指标归属，说明两处结果是否来自相同的评估条件，并明确“不超过3分”所采用的完整模型基线及对应指标。该澄清关系到读者能否准确理解适配阶段蒸馏的增益，以及学生模型与完整模型之间的性能差距。

原文位置：PDF page 3; PDF page 4; Table 2, PDF page 3.

建议作者回应：请核对正文及表2中的EM/F1数值、指标顺序和模型行归属；说明两处结果所用的SQuAD版本与数据划分、评估程序、教师与学生检查点及适配配置是否一致。若配置不同，请注明各组结果的条件；若属于表述或排版问题，请统一更正。请同时列明“不超过3分”所指的完整模型基线及其分数，并在可比条件下说明适配阶段蒸馏相对于普通DistilBERT的增益及其与完整模型的差距。

- P3: concern_narrowed; report_eligibility: requires_human_review; human_review_status: pending; reviewer_approval_status: pending
- 未完成检查：Does the original PDF confirm the extracted prose values of 79.8 F1 and 70.4 EM, including their metric labels?; Does the original Table 2 confirm the EM/F1 ordering and the associations BERT-base 81.2/88.5, DistilBERT 77.7/85.8, and DistilBERT (D) 79.1/86.9?; Do the prose and table use the same SQuAD version and split, evaluation procedure, teacher checkpoint, student checkpoint, and adaptation configuration?

### 3. 澄清预训练成本的核算边界与比较条件

摘要及引言将较低的预训练成本作为作者方法的优势；“Data and compute power”段落报告了 DistilBERT 的训练资源，并引用 RoBERTa 的资源用量作为比较。需要澄清的科学问题是：在明确计入或分摊教师预训练与教师计算成本后，现有资源证据是否足以支持较低预训练成本的主张，以及相关比较是否考虑了数据暴露量和目标性能。作者已说明学生与原始 BERT 使用相同语料，并介绍了教师层初始化及蒸馏训练，这些信息有助于界定比较背景；不过，相同语料本身并不意味着相同的数据暴露量，所列资源用量也需要结合成本边界和训练负载解释。请明确所称成本是已有教师条件下训练学生的增量成本，还是获得教师和学生的总成本，并说明教师预训练成本是否计入或如何分摊，以及所报训练时间是否包含教师前向计算和任何教师输出缓存的准备。请同时说明 RoBERTa 的资源数字仅用于展示训练资源规模，还是用于支持成本优势，并交代相应比较的模型配置、数据暴露量、序列长度、训练工作量及所达性能。若已有可比的 BERT 资源记录，可据此补充说明；若现有记录不足以支持受控比较，可将结论限定为已报告条件下的学生训练资源需求。这些澄清将有助于读者判断成本优势的适用条件。

原文位置：PDF page 1; PDF page 2; PDF page 3; PDF page 4.

建议作者回应：请补充成本核算口径，说明教师预训练、教师前向计算及缓存准备是否包含在所报资源中，并说明共享教师成本的分摊方式；明确 RoBERTa 比较的用途及其与学生训练的可比条件。可优先利用已有训练记录说明数据暴露量、序列长度、训练工作量与目标性能；如无法建立相应比较，请收窄较低预训练成本的表述，使其与现有证据的适用范围一致。

- P3: inconclusive; report_eligibility: requires_human_review; human_review_status: pending; reviewer_approval_status: pending
- 未完成检查：Does the intended cost boundary cover incremental student training given an existing teacher, or the total cost of obtaining both teacher and student, and how is any shared teacher cost amortized?; Does the approximately 90-hour duration include all teacher forward passes, any preparation of cached teacher outputs, and other preparation required for the reported student training?; Is there a relevant BERT training-resource comparison with comparable token exposure, sequence lengths, optimization workload, and a stated target performance? Sharing the same corpus does not establish equal data exposure.

### 4. 澄清蒸馏损失联合贡献的对照条件

表4及其消融分析讨论了作者提出的训练目标中各项损失和教师权重初始化的作用。请澄清：表4或论文其他材料是否将完整目标与一个采用相同教师权重初始化、仅使用掩码语言建模损失 Lmlm 训练的学生模型进行比较，即同时移除输出分布蒸馏损失 Lce 和隐状态对齐损失 Lcos，并保持其他相关条件一致？现有文字表明作者已考察损失组成和初始化的影响；所提供的表格提取内容似乎主要涉及逐项移除损失，但表格配置及其数值对应关系仍需核实。逐项移除所反映的是某项损失在其余损失保留时的作用，不能直接确定两项蒸馏损失相对于纯语言建模训练的联合增益。这一区别关系到如何理解文中关于蒸馏损失对性能贡献的表述。请指出已有匹配对照的位置及配置，并说明架构、教师权重初始化、训练语料、掩码方式、优化设置、训练词元量或预算以及下游微调条件是否一致。若未开展该对照，请明确现有消融支持的结论范围，并相应限定联合贡献的归因表述。

原文位置：PDF page 2; PDF page 3; PDF page 4; PDF page 5.

建议作者回应：请说明是否已有同时移除 Lce 和 Lcos、保留教师权重初始化的 Lmlm-only 对照；若有，请提供其位置、配置和相关训练及评估条件的匹配说明，并据此解释联合贡献。若无，请明确现有消融仅能支持的条件性结论，并调整相关归因措辞。

- P3: inconclusive; report_eligibility: requires_human_review; human_review_status: pending; reviewer_approval_status: pending
- 未完成检查：Does the original Table 4 confirm the extracted loss configurations and their numerical associations?; Does any other paper material report a teacher-initialized Lmlm-only student with both Lce and Lcos removed?; Across the relevant controls, are architecture, training corpus, masking procedure, optimization settings, token exposure, and downstream fine-tuning held constant?
- P3: inconclusive; report_eligibility: requires_human_review; human_review_status: pending; reviewer_approval_status: pending
- 未完成检查：Does the complete paper include an MLM-only compact student with the same architecture, teacher-weight initialization, corpus, masking procedure and training budget as the full recipe?; Does any experiment compare pretraining distillation against ta[REDACTED] distillation with matched student architecture, initialization, training resources and downstream evaluation?; Does the original Table 4 confirm the extracted configurations and numerical associations, and does it contain any configuration removing both Lce and Lcos simultaneously?

### 5. 明确性能保留比例与 GLUE 宏平均分的计算方式

关于摘要、结论及表 1 附近所述“保留 97% 的语言理解能力／性能”，请明确该比例是否由 DistilBERT 与 BERT-base 的 GLUE 宏平均分相除并四舍五入得到。实验正文已将宏平均分定义为各项分数的平均值，并在同一语境中给出性能保留表述；表注也说明结果来自开发集，BERT 与 DistilBERT 的结果采用不同随机种子运行的中位数。这些说明支持将该比例理解为 GLUE 汇总结果的比较，但尚未明确比例公式及完整的汇总规则。请在表注或实验说明中列出各任务采用的指标，说明同一任务包含多个指标或数据划分时如何合并，并交代是先求各任务跨随机种子的中位数再求宏平均，还是先求每次运行的宏平均再取中位数。同时，建议在摘要和结论中明确该比例对应的 GLUE 汇总指标范围。上述澄清有助于读者复现汇总结果，并准确理解总体性能保留表述的含义。

原文位置：PDF page 1; PDF page 3; PDF page 5.

建议作者回应：请给出性能保留比例的明确公式及对应的模型汇总分，补充各任务指标、多指标或多划分的合并规则，以及跨随机种子取中位数与跨任务求平均的先后顺序；并将摘要和结论中的相关表述明确限定到该指标所支持的范围。可通过补充表注或实验文字完成说明。

- P3: inconclusive; report_eligibility: requires_human_review; human_review_status: pending; reviewer_approval_status: pending
- 未完成检查：Does inspection of the original table confirm that 77.0 and 79.5 are the DistilBERT and BERT-base macro-scores, respectively, and that their ratio is the intended calculation behind 97%?; Which metric or combination of metrics supplies each of the nine task scores, how are multiple splits handled, and is the aggregate the average of task medians or the median of run-level averages?; What task-level differences are confirmed by the original table, and how materially do their magnitudes qualify the aggregate retention claim? The extracted row alignment suggests variation, but does not establish verified task-to-score associations.

### 6. 澄清预训练蒸馏与仅任务特定蒸馏的比较范围

相关工作中“Task-specific distillation”段落称，相较于任务特定蒸馏，采用通用预训练蒸馏是有益的。请澄清这一表述是在说明作者的方法选择，还是在主张预训练蒸馏在可比条件下具有性能优势。作者已描述学生架构、从教师层初始化的方法、预训练语料及计算资源；消融分析的文字主要讨论预训练损失组成和初始化，而 SQuAD 实验描述的是在预训练蒸馏之后追加任务特定蒸馏。这些内容有助于理解作者的方法及不同阶段的兼容性，但仅凭现有材料，尚不能确定全文是否直接比较了预训练蒸馏与仅任务特定蒸馏，并充分匹配学生架构、初始化、数据访问及训练预算。区分这些证据的适用范围，有助于读者判断性能收益能否归因于蒸馏发生的阶段。请指明支持该比较的实验及其控制条件；若该句主要表达方法选择，或现有实验并未单独区分蒸馏阶段的作用，建议相应限定措辞，突出已展示的通用预训练蒸馏效用。

原文位置：PDF page 1; PDF page 2; PDF page 3; PDF page 4; PDF page 5; Table 2, PDF page 3; Table 4, PDF page 4.

建议作者回应：请解释相关工作中比较性表述的确切含义，并指出全文中对应的直接比较（如有），说明学生架构、初始化、教师配置、数据访问和训练预算的匹配情况及差异。若没有隔离蒸馏阶段作用的比较，请将该表述限定为现有实验支持的方法效用或设计选择，并明确损失消融及追加任务特定蒸馏实验各自回答的问题；可通过文字澄清和定位已有证据回应。

- P3: inconclusive; report_eligibility: requires_human_review; human_review_status: pending; reviewer_approval_status: pending
- 未完成检查：Does the complete paper contain a pretraining-distillation versus ta[REDACTED] comparison with matched student architecture, initialization, teacher configuration, data access and training budget?; Does the original layout and surrounding text clarify whether 'beneficial ... rather than' expresses a design choice or a claim of comparative superiority?; If no stage-isolating comparison exists, would revising that sentence to describe the demonstrated utility of general-purpose pretraining distillation and its additional teacher signals better align the wording with the evidence?

### 7. 澄清推理耗时、速度提升定义及计时条件

关于表3及设备端计算段落中的推理效率比较，请作者澄清原始耗时、速度提升百分比的定义及相应计时配置。作者已说明CPU比较使用的处理器、STS-B开发集和批大小，也交代了移动端设备、问答比较对象及排除分词时间的处理。这些信息为理解比较提供了基础，但尚不足以判断两种模型是否在一致的输入和运行条件下计时，也不足以复现所报告的效率收益。

请结合原始表3和对应计时代码，明确各模型的耗时、单位，以及百分比计算的分母和舍入规则，说明“更快”具体指耗时降低还是吞吐率提高。对于CPU测试，请补充框架及版本、执行后端、数值精度、线程设置、序列长度处理和计时起止边界。对于移动端测试，请补充原始耗时、所用模型检查点、问答输入及长度处理规则、运行后端和软件版本、数值精度与设备执行设置，并说明除分词外还有哪些操作计入或排除。请同时交代各项比较是否使用相同输入和匹配的运行设置，以及预热、重复测量、必要的同步和耗时汇总方式。若相关信息已包含在公开代码中，提供与报告结果对应的版本及计时入口即可。上述澄清有助于区分模型压缩带来的效率收益与实现或测量条件的影响。

原文位置：PDF page 2; PDF page 3; PDF page 4.

建议作者回应：请核对原始表3，提供模型与原始耗时的对应关系及速度提升百分比的计算定义；补充CPU和移动端测试的运行配置、输入处理、计时边界与测量汇总程序，并标明可对应到所报告结果的代码或应用版本。可通过补充方法说明、配置记录及已有计时代码回应。

- P3: inconclusive; report_eligibility: requires_human_review; human_review_status: pending; reviewer_approval_status: pending
- 未完成检查：Does the original Table 3 confirm the apparent association of BERT-base with 668 seconds and DistilBERT with 410 seconds, and what percentage definition and rounding produced the reported 60% faster claim?; For the CPU benchmark, what framework and version, numerical precision, execution backend, CPU thread settings, sequence-length handling, and timing boundaries were used?; For the mobile benchmark, what question-answering examples, context and question lengths, truncation or windowing rules, and model checkpoint variants were used?

### 8. 澄清训练配置及其与已发布代码和模型的对应关系

针对训练与实验部分中的蒸馏设置、数据与计算资源说明，以及文中指向的代码发布入口，请作者明确：用于生成所报告模型的优化器、学习率调度、更新总量、有效批量与序列长度安排、掩码设置及教师检查点，分别在哪里可以查得。作者已说明采用梯度累积、动态掩码、不使用下一句预测目标，并描述了学生架构、从教师交替层初始化的方法及训练目标；代码和权重的公开声明也为查明其余配置提供了途径。不过，当前可核查材料仅包含发布声明和仓库链接，尚不能确定具体脚本、版本及配置与论文实验的对应关系。这些信息关系到训练过程能否重建，以及读者如何理解模型比较所依据的训练条件。建议作者提供与论文结果对应的仓库修订版本、配置文件和启动命令，并标明相关参数；如这些信息已在完整论文或公开脚本中给出，提供准确位置即可。

原文位置：PDF page 2; PDF page 3; PDF page 4.

建议作者回应：请提供对应论文实验的代码修订版本、训练配置及启动命令，列明优化器及其参数、学习率与预热安排、总更新步数或处理的词元总量、包含梯度累积的有效批量安排、序列长度安排、掩码概率与替换规则，以及教师检查点和相应分词器配置。对于已经公开的信息，请给出准确的章节、文件路径或配置入口，并说明其对应的模型权重。

- P3: inconclusive; report_eligibility: requires_human_review; human_review_status: pending; reviewer_approval_status: pending
- 未完成检查：What optimizer, optimizer parameters, learning-rate and warmup schedules, total updates or processed tokens, effective-batch schedule, and sequence-length schedule produced the reported pretrained model?; What masking probabilities and replacement rules were used, and what exact teacher checkpoint, tokenizer, casing configuration, and teacher-layer selection were used?; Which corpus versions, filtering, preprocessing, document segmentation, and example-packing procedures reconstruct the Wikipedia and Toronto Book Corpus training examples?

## 核验限制与来源

- consolidated_c9750ce2c32914386e93: inconclusive / requires_human_review; Do the original source and experiment-specific code and configurations uniquely specify the reported triple objective, including loss coefficients, training temperature and scaling, contributing token positions, hidden-state pairing, reductions, and coefficient handling in the ablations?
  - 待检查：What numerical coefficients weight distillation, masked language modeling, and cosine alignment in the reported pretraining run?; What numerical training temperature was used, and was the distillation loss multiplied by a temperature-dependent factor such as T squared?; Which token positions contribute to each loss, including treatment of padding, special tokens, masked positions, and unmasked positions?
- consolidated_2fc7365d65828d021acd: concern_narrowed / requires_human_review; Could the authors reconcile the located adaptation-distillation prose reporting 70.4 EM and 79.8 F1 with Table 2's extracted DistilBERT (D) values of 79.1 EM and 86.9 F1, confirming the table associations and evaluation configurations and identifying the full-model baseline intended by 'within 3 points'?
  - 待检查：Does the original PDF confirm the extracted prose values of 79.8 F1 and 70.4 EM, including their metric labels?; Does the original Table 2 confirm the EM/F1 ordering and the associations BERT-base 81.2/88.5, DistilBERT 77.7/85.8, and DistilBERT (D) 79.1/86.9?; Do the prose and table use the same SQuAD version and split, evaluation procedure, teacher checkpoint, student checkpoint, and adaptation configuration?
- consolidated_74d31eae282a7e352cfb: inconclusive / requires_human_review; Does the reported training-resource evidence support the lower-pretraining-cost claim under an explicit accounting boundary for teacher pretraining and teacher computation, with a relevant comparison controlling data exposure and target performance?
  - 待检查：Does the intended cost boundary cover incremental student training given an existing teacher, or the total cost of obtaining both teacher and student, and how is any shared teacher cost amortized?; Does the approximately 90-hour duration include all teacher forward passes, any preparation of cached teacher outputs, and other preparation required for the reported student training?; Is there a relevant BERT training-resource comparison with comparable token exposure, sequence lengths, optimization workload, and a stated target performance? Sharing the same corpus does not establish equal data exposure.
- consolidated_8e8e2b5dcfc41219ae35: inconclusive / requires_human_review; Does Table 4 or other available material compare the full objective with an otherwise matched, teacher-initialized student trained with masked language modeling alone, removing both distillation losses, and thereby establish their combined contribution rather than only conditional component benefits?
  - 待检查：Does the original Table 4 confirm the extracted loss configurations and their numerical associations?; Does any other paper material report a teacher-initialized Lmlm-only student with both Lce and Lcos removed?; Across the relevant controls, are architecture, training corpus, masking procedure, optimization settings, token exposure, and downstream fine-tuning held constant?
- consolidated_571a39dcba42a9759ff6: inconclusive / requires_human_review; Does the verified table establish that 97% is calculated as the ratio of GLUE macro-scores, and how are individual task metrics combined?
  - 待检查：Does inspection of the original table confirm that 77.0 and 79.5 are the DistilBERT and BERT-base macro-scores, respectively, and that their ratio is the intended calculation behind 97%?; Which metric or combination of metrics supplies each of the nine task scores, how are multiple splits handled, and is the aggregate the average of task medians or the median of run-level averages?; What task-level differences are confirmed by the original table, and how materially do their magnitudes qualify the aggregate retention claim? The extracted row alignment suggests variation, but does not establish verified task-to-score associations.
- consolidated_63e4fcc89ffc9ebae2cf: inconclusive / requires_human_review; Does the complete paper compare pretraining distillation with task-specific-only distillation under sufficiently matched student architecture, initialization, data, and training budget?
  - 待检查：Does the complete paper contain a pretraining-distillation versus ta[REDACTED] comparison with matched student architecture, initialization, teacher configuration, data access and training budget?; Does the original layout and surrounding text clarify whether 'beneficial ... rather than' expresses a design choice or a claim of comparative superiority?; If no stage-isolating comparison exists, would revising that sentence to describe the demonstrated utility of general-purpose pretraining distillation and its additional teacher signals better align the wording with the evidence?
- consolidated_84cf783ca167b1714267: inconclusive / requires_human_review; Inspect the original Table 3 and timing code for raw durations, the percentage calculation, framework version, numerical precision, CPU thread settings, and timing boundaries.
  - 待检查：Does the original Table 3 confirm the apparent association of BERT-base with 668 seconds and DistilBERT with 410 seconds, and what percentage definition and rounding produced the reported 60% faster claim?; For the CPU benchmark, what framework and version, numerical precision, execution backend, CPU thread settings, sequence-length handling, and timing boundaries were used?; For the mobile benchmark, what question-answering examples, context and question lengths, truncation or windowing rules, and model checkpoint variants were used?
- consolidated_1ca04a734f00044fd43b: inconclusive / requires_human_review; Check the complete training and experiments sections and released scripts for the optimizer, learning-rate schedule, update count, effective batch schedule, sequence lengths, masking settings, and teacher checkpoint.
  - 待检查：What optimizer, optimizer parameters, learning-rate and warmup schedules, total updates or processed tokens, effective-batch schedule, and sequence-length schedule produced the reported pretrained model?; What masking probabilities and replacement rules were used, and what exact teacher checkpoint, tokenizer, casing configuration, and teacher-layer selection were used?; Which corpus versions, filtering, preprocessing, document segmentation, and example-packing procedures reconstruct the Wikipedia and Toronto Book Corpus training examples?
- consolidated_0caacb13a2bb7f01cbb8: inconclusive / requires_human_review; Does the complete paper report a compact-model baseline using masked language modeling alone with matched architecture, teacher-weight initialization, data, and training budget?
  - 待检查：Does the complete paper include an MLM-only compact student with the same architecture, teacher-weight initialization, corpus, masking procedure and training budget as the full recipe?; Does any experiment compare pretraining distillation against ta[REDACTED] distillation with matched student architecture, initialization, training resources and downstream evaluation?; Does the original Table 4 confirm the extracted configurations and numerical associations, and does it contain any configuration removing both Lce and Lcos simultaneously?
- 41 项解析覆盖疑点保留在冻结来源中，需按相关原文逐项复核。
- 5 项表格结构限制保留在冻结来源中。
- 2 项图像或图注限制保留在冻结来源中。
- The supplied quality state marks reading order as ambiguous and section mapping as incomplete; human reading and page references are unavailable.
- The ablation deltas extracted in ru_ev_6724ca5dce54_3_2 have unverified table associations, so exact component-specific numerical effects remain unresolved.
- The distillation and temperature equations are fragmented across reading units; exact formula transcription, including signs and normalization, remains unverified.
- Source quality: reading_order_ambiguous
- Source quality: section_mapping_incomplete
- Source quality: human_reading_reference_missing
- Source quality: human_page_reference_missing
