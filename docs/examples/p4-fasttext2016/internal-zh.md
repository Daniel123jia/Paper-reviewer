# 待审核草稿 / DRAFT

此版本尚未完成审稿人最终审核。

# 内部完整审稿报告

## 报告范围

本报告按 9 个审查主题保留 9 项 P3 核验问题，其中 1 项具备限定性自动草稿资格。以下意见保留原始判定与待核验事项，仅供审稿人内部审阅，不代表已确认的论文错误。

## 需要澄清的问题

### 1. 澄清情感分类加速倍数的来源及训练时间口径

情感分类实验的训练时间段落提出“至少 15,000 倍”的加速。作者已在表 2 的标题中明确说明计时针对单个训练轮次，在结果段落中说明 fastText 使用 5 轮训练，并披露卷积基线使用 GPU、fastText 使用 20 线程 CPU，以及 GRNN 的单线程 CPU 计时条件。这些说明有助于理解比较，但仍需明确：哪一个数据集、基线及模型配置产生了所述倍数，其分子与分母分别对应何种训练范围？尤其是，将产生所报告准确率所需的基线训练轮次或停止条件，以及 fastText 的 5 轮训练纳入后，完整训练的时间比是多少？这一口径直接影响读者能否将单轮吞吐优势解释为达到所报告预测性能所需的训练时间优势。请作者给出该倍数的具体来源与计算式，说明准确率结果和计时结果是否对应相同模型配置，并补充已有的训练轮次、停止条件及计时范围说明。如果现有记录尚不能建立完整训练时间比，建议将情感分类的加速表述明确限定为已披露硬件、线程数和实现条件下的单轮训练比较。

原文位置：PDF page 1; PDF page 2; PDF page 3; PDF page 4; Table 2, PDF page 3.

建议作者回应：请指明产生“至少 15,000 倍”加速的数据集、比较模型及配置，列出所用时间值、单位、计时范围和计算式，并说明这些配置与准确率结果的对应关系。若已有记录支持，请结合各基线的训练轮次或停止条件及 fastText 的 5 轮训练给出完整训练时间比；若尚不支持，请将相关声明限定为所披露执行条件下的单轮计时，并说明其适用范围。

- P3: concern_narrowed; report_eligibility: eligible_with_qualification; human_review_status: pending; reviewer_approval_status: pending
- 未完成检查：Which dataset, comparator and model configuration produce the stated at-least-15,000-fold speedup, and is its denominator a single epoch or a complete training run?; What epoch counts and stopping criteria produced each reported classification baseline accuracy, and what complete-training ratios result when those schedules and the five fastText epochs are included?; Are the classification accuracy entries and timing entries paired to precisely the same baseline configurations, including model size, depth and use of pretrained embeddings?

### 2. 请明确 YFCC100M 数据划分的重建依据

在 YFCC100M 实验的“Dataset and baselines”段落中，作者说明了以标题和说明文字预测标签的任务、低频词与标签的过滤条件，以及训练集、验证集和测试集的规模，并承诺发布重建该数据集的脚本。需要进一步明确的科学问题是：原始任务说明或配套重建材料是否提供了各划分的确切记录标识、确定性的分配规则，或随机划分所需的种子及输入顺序。仅有各划分的规模尚不足以唯一确定其记录组成，而记录组成是否一致关系到后续研究能否在相同数据条件下复现和比较结果。请作者指出适用于该实验的重建脚本或任务说明的具体位置与版本，并说明其中如何确定每条记录所属的划分；如采用随机分配，请给出随机种子及所需的输入排序约定，如采用固定划分，请提供记录清单或可确定相同记录集合的规则。若这些信息已包含在配套材料中，在正文中补充明确引用即可。

原文位置：PDF page 1; PDF page 4; PDF page 5.

建议作者回应：提供与 YFCC100M 实验对应的重建材料位置和版本，并明确记录标识或确定性划分规则；若使用随机划分，补充随机种子与输入顺序约定。已有相关说明时，请给出可直接定位的引用。

- P3: inconclusive; report_eligibility: requires_human_review; human_review_status: pending; reviewer_approval_status: pending
- 未完成检查：Do the reconstruction materials specify exact record IDs or a deterministic assignment procedure, including any random seed and required input ordering?; Does 'occurring less than 100 times' refer to total token occurrences or the number of records containing each word or tag, and what tokenization and normalization precede counting?; How are records with empty title-and-caption text or no remaining tags handled after filtering?

### 3. 澄清 Huffman 层次 softmax 训练复杂度的平均含义

在介绍 Huffman 层次 softmax 的方法段落中，作者将训练复杂度表述为 O(h log₂(k))，其中 h 为文本表示维度，k 为类别数。请明确：原文或相关上下文是否将这一复杂度定义为相对于建树所用频率分布的期望计算量？这一限定关系到读者如何理解该表述的适用范围，以及如何将其与特定目标对应的路径计算量区分开来。建议说明 Huffman 树所采用的频率统计口径，以及训练目标的采样或加权分布是否与其一致；对于包含多个标签的文档，也请说明标签如何计入建树频率和训练目标。若该表述指频率加权的平均复杂度，请在原句附近明确写出这一含义及所需条件；若指其他复杂度口径，请给出相应定义与依据。现有实验中的运行效率描述有助于说明方法的实际表现，但尚不能单独确定该复杂度表述采用的平均方式。

原文位置：PDF page 2; PDF page 4.

建议作者回应：请指出原文中已有的相关限定，或在 Huffman 层次 softmax 的训练复杂度陈述附近补充定义：说明建树频率的统计口径、训练目标分布及多标签处理方式，并明确该复杂度是何种分布下的期望或平均计算量，以及这一表述所依赖的路径更新条件。此项可通过文字说明和必要的推导予以回应。

- P3: inconclusive; report_eligibility: requires_human_review; human_review_status: pending; reviewer_approval_status: pending
- 未完成检查：Does the original PDF or other surrounding material explicitly qualify the training bound as an average under the frequencies used to construct the Huffman tree?; Which counts determine the tree: document-level label occurrences, tag occurrences, sampled training targets, or another quantity?; Does training sample targets according to the same distribution used to construct the tree, particularly for documents containing multiple tags?

### 4. 澄清平均特征表示的适用范围及其与 word2vec 的对照依据

结论将作者学习的 fastText 特征经平均后形成良好句子表示的能力，与无监督训练的 word2vec 向量作了对照。方法部分说明，这些平均特征构成输入线性分类器的隐表示，并通过文档标签进行监督训练；作者报告的情感分类和标签预测评估，因此为其在相应监督分类设置中的实用性提供了依据。建议明确，结论中的“良好句子表示”是否限于这些已评估的监督分类设置，并说明与平均 word2vec 向量对照的证据或预期限定条件。这一澄清有助于读者区分任务监督下学得的表示、相应分类器的表现，以及表示复用于其他任务的可能性。作者可通过限定结论措辞来回应；若保留该对照，请指出已有比较或文献依据，并交代监督信息、下游分类器、训练数据及 n-gram 特征等相关比较条件。

原文位置：PDF page 1; PDF page 2; PDF page 3; PDF page 4; PDF page 5.

建议作者回应：请在结论中明确平均 fastText 特征的已验证用途及适用范围。若与平均无监督 word2vec 向量的对照意在表达监督训练目标的差异，请相应限定措辞；若意在表达性能优势，请指出已有证据及其比较条件。对于表示复用，请区分方法上的潜在用途与已报告的评估结果。

- P3: concern_narrowed; report_eligibility: requires_human_review; human_review_status: pending; reviewer_approval_status: pending
- 未完成检查：Does the complete paper contain a comparison of averaged word2vec vectors and fastText features using the same downstream classifier, evaluation datasets, embedding dimensions and comparable training-data conditions, with frozen versus task-adapted embeddings distinguished?; How much of any comparative advantage comes from label supervision and joint classifier training, rather than the learned features or averaging operation?; Do matched word-only comparisons support the word2vec contrast independently of n-gram features? The supplied within-fastText ablations address n-gram contributions but do not isolate a difference from word2vec.

### 5. 澄清不同类别数量下推断效率的测量依据

在层次 softmax 的测试时搜索说明及大规模标签预测实验中，作者描述了利用节点概率进行深度优先搜索和分支剪枝的方法，并将对数形式的推断复杂度表述为实践中的观察。大标签空间基准提供了相关效率证据，但现有摘录尚不能确定，完整论文是否还报告了类别数量变化时的节点访问量或推断耗时；固定标签集合下改变隐藏层维度的比较，也不能直接回答这一问题。请作者指出全文中是否已有此类测量及其具体位置，并说明相应的类别数量设置、耗时统计范围和主要比较条件。这有助于读者区分特定大标签任务上的效率优势与跨类别规模的经验性扩展规律。若全文未提供此类测量，请明确现有结果所支持的范围，并说明经验性复杂度观察所依据的条件。

原文位置：PDF page 1; PDF page 2; PDF page 3; PDF page 4.

建议作者回应：请定位并概述全文已有的跨类别数量节点访问量或推断耗时测量，说明类别规模如何设置、计时涵盖哪些操作，以及主要比较条件。若未提供此类测量，请明确大标签空间基准与经验性复杂度表述的证据范围及适用条件。

- P3: inconclusive; report_eligibility: requires_human_review; human_review_status: pending; reviewer_approval_status: pending
- 未完成检查：Does the complete paper contain visited-node counts or prediction-runtime measurements across class counts? The supplied excerpts establish one large-output benchmark, but cannot establish the absence of other measurements from the complete PDF.; Within the stated depth-first traversal, how are children ordered, how is the pruning threshold initialized and updated, and what stopping and tie-handling rules are used? Does the implementation return exact top-T predictions under the hierarchical model?; Does O(log(T)) describe one heap update, overhead on each visited node or candidate leaf, or another quantity? What is total prediction cost in terms of visited internal nodes, candidate leaves, representation dimension h, and requested output count T?

### 6. 请明确运行时间比较的 CPU 配置与计时范围

关于情感分类与标签预测实验中的效率比较，请明确完整实验说明或实现文档是否给出了 CPU 型号，以及各项训练和测试计时所包含的操作。相关位置为表 2、表 5 及其相邻的训练与测试时间讨论。作者已说明情感分类的单轮训练计时，并交代了部分硬件、线程设置，以及标签预测实验中 fastText 的训练轮次；这些信息为理解比较条件提供了依据。不过，现有材料仍无法确定 CPU 的具体配置、标签预测模型是否使用相同硬件，以及各计时区间是否涵盖数据预处理与加载、词表建立、特征哈希、Huffman 树构建、初始化和模型加载。这些定义会影响读者对作者所报告效率优势的解释及其复现。建议作者按实验和模型列明硬件配置、计时起止点及包含或排除的操作，并说明表 5 的训练时间所覆盖的训练轮次、测试时间所覆盖的数据范围，以及 Tagspace 的训练预算和停止条件。另请说明情感分类中“训练少于一分钟”的表述与单轮计时及完整训练过程的对应关系。如这些信息已在论文或实现文档中给出，提供明确位置即可。

原文位置：PDF page 3; PDF page 4.

建议作者回应：请提供已有硬件和计时协议说明的准确位置，或补充一份按实验及模型组织的说明，列出 CPU 型号与处理器配置、硬件是否一致、训练与测试计时起止点、所包含和排除的操作、训练预算及测试覆盖范围，并澄清情感分类训练时间表述的适用范围。

- P3: inconclusive; report_eligibility: requires_human_review; human_review_status: pending; reviewer_approval_status: pending
- 未完成检查：What CPU model and processor configuration were used for sentiment and tag-prediction timing, and did both tag-prediction models run on identical hardware?; For each training and test timer, were preprocessing, vocabulary creation, feature hashing, Huffman-tree construction, data loading, initialization, and model loading included or excluded?; Does each fastText training time in Table 5 cover all five epochs, and does its test time cover the complete 543,424-example test set?

### 7. 澄清系统整体效率与算法组件加速贡献的解释范围

关于全文是否充分区分端到端系统效率与特定算法组件带来的加速，建议在实验设置、训练与预测计时结果及相关效率结论中进一步明确解释范围。作者已说明定制实现相对于 Vowpal Wabbit 的实现优势，解释了层次 softmax 及树搜索减少计算的机制，并披露了情感分类比较中的执行配置差异，以及标签预测比较中的线程设置。这些说明为理解效率来源提供了依据。不过，仅凭现有材料，尚不能确定实测速度差异中哪些可归于整体系统，哪些可单独归于层次推断、实现或并行化。该区分关系到读者能否将报告的速度优势推广到其他实现与运行条件。请明确各项比较的计时口径，例如单轮训练、完整训练或达到可比预测性能所需的时间，并说明关键模型配置、硬件与线程条件是否匹配。对于组件层面的加速表述，请指向已有的受控比较；若现有实验未单独量化组件贡献，可直接限定相关结论的归因范围。作者可通过补充比较条件说明或交叉引用已有材料回应，无需为此默认增加实验。

原文位置：PDF page 1; PDF page 2; PDF page 3; PDF page 4.

建议作者回应：请在相关计时结果及效率结论附近明确计时边界、可比预测性能要求和运行配置，说明哪些结果支持系统整体效率，哪些已有受控比较支持特定组件的贡献；若无法单独归因，请相应限定表述，并指向全文中已有的说明或比较。

- P3: inconclusive; report_eligibility: requires_human_review; human_review_status: pending; reviewer_approval_status: pending
- 未完成检查：For the Vowpal Wabbit comparison, were datasets, features, hidden dimensions, loss and inference procedures, epochs, hardware, thread counts, and achieved accuracy matched, and which timing boundaries produced the reported 2–5 times difference?; Is there a same-model thread-scaling comparison that quantifies the contribution of parallelism separately from implementation and algorithm choices?; Is hierarchical softmax or tree-based inference compared with exhaustive scoring within the same fastText implementation and model configuration, especially on YFCC100M?

### 8. 请明确词与标签频次阈值的统计范围

在 YFCC100M 的“Dataset and baselines”段落中，作者描述先移除出现次数少于 100 的词和标签，再划分训练集、验证集和测试集。这一表述在字面顺序上提示筛选可能先于划分，但尚未明确频次究竟基于整个语料、预先筛选的候选子集，还是仅基于训练集计算。该统计范围决定了词表和标签集合如何形成，也影响读者对基准数据构建流程的理解与复现。请作者在正文或数据重建脚本说明中明确频次统计所用的数据范围，以及统计、筛选和数据划分的实际执行顺序，并提供相应的脚本位置或伪代码。若采用全语料统计，请明确记录这一基准构建约定。

原文位置：PDF page 4.

建议作者回应：明确词频和标签频次各自的统计数据范围，说明频次统计、阈值筛选与数据划分的执行顺序，并通过正文说明、数据重建脚本位置或简短伪代码使该流程可复现。

- P3: inconclusive; report_eligibility: requires_human_review; human_review_status: pending; reviewer_approval_status: pending
- 未完成检查：Are word and tag frequencies counted over the full corpus, a preliminary eligible subset, or only the training partition?; Does filtering remove only word and tag types, or also examples, and how are examples with no retained tags handled?; Is precision at one scored against all original tags or only retained tags, and which examples enter its denominator?

### 9. 澄清多标签记录的训练目标构造及评估定义

在模型说明与 YFCC100M 标签预测部分，需要明确的问题是：同一记录包含多个标签时，这些标签如何转换为训练目标？作者在模型描述中使用标签变量及类别概率分布，并在标签预测部分说明了频次过滤、数据划分和首位预测精确率（precision at one）等设置；但仅依据目前可读的材料，尚无法确定多标签记录与训练损失的具体对应方式。请说明实际采用的是抽取一个保留标签、将记录展开为多个记录—标签训练对，还是对多个标签共同计算损失，并定义相应的归一化或权重。还请交代过滤后无保留标签的记录在各数据划分中如何处理，首位预测是否以命中任一保留标签为正确及其分母包含哪些记录，以及一个训练轮次所遍历的单位。上述定义关系到每条记录对训练的贡献、评估指标的解释及训练时间比较的可复现性。请同时说明 fastText 与线性 Tagspace 基线在保留目标、记录权重和评估参考标签方面是否使用相同条件。若完整论文或对应实现已给出这些定义，提供准确位置并在实验协议中作简要说明即可。

原文位置：PDF page 1; PDF page 2; PDF page 4.

建议作者回应：请补充一段训练与评估协议说明，明确多标签目标的构造、损失归一化或权重、无保留标签记录的处理、首位预测精确率的参考标签集合与分母、训练轮次的遍历单位，以及 fastText 与线性 Tagspace 在这些条件上的一致性或差异；如已有明确说明，请提供对应章节或实现位置。

- P3: inconclusive; report_eligibility: requires_human_review; human_review_status: pending; reviewer_approval_status: pending
- 未完成检查：For YFCC100M training, is one retained tag sampled per record, is each record expanded into separate tag targets, or is a loss computed over several tags? What normalization or weighting is used?; Are records with no tags remaining after frequency filtering discarded, retained without updates, or handled otherwise in each split?; Does precision at one count a prediction as correct when it matches any retained reference tag, and which records enter its denominator?

## 核验限制与来源

- consolidated_8103558cbdda88bc3c6c: concern_narrowed / eligible_with_qualification; For the sentiment-classification comparisons, which verified dataset and model configuration yield the stated at-least-15,000-fold speedup, and what complete-training ratio follows when the baseline schedules and five fastText epochs used for the reported accuracies are included? If that ratio is not established, should the classification speedup statement be explicitly limited to single-epoch timing under the disclosed execution conditions?
  - 待检查：Which dataset, comparator and model configuration produce the stated at-least-15,000-fold speedup, and is its denominator a single epoch or a complete training run?; What epoch counts and stopping criteria produced each reported classification baseline accuracy, and what complete-training ratios result when those schedules and the five fastText epochs are included?; Are the classification accuracy entries and timing entries paired to precisely the same baseline configurations, including model size, depth and use of pretrained embeddings?
- consolidated_456a6e99f6fb075f3912: inconclusive / requires_human_review; Do the original task description or accompanying reconstruction materials specify exact record IDs, a deterministic assignment rule, or a random seed for the splits?
  - 待检查：Do the reconstruction materials specify exact record IDs or a deterministic assignment procedure, including any random seed and required input ordering?; Does 'occurring less than 100 times' refer to total token occurrences or the number of records containing each word or tag, and what tokenization and normalization precede counting?; How are records with empty title-and-caption text or no remaining tags handled after filtering?
- consolidated_64b696e6ae9642e79b3d: inconclusive / requires_human_review; Does the original passage or surrounding material define this as expected complexity under the frequencies used to construct the Huffman tree?
  - 待检查：Does the original PDF or other surrounding material explicitly qualify the training bound as an average under the frequencies used to construct the Huffman tree?; Which counts determine the tree: document-level label occurrences, tag occurrences, sampled training targets, or another quantity?; Does training sample targets according to the same distribution used to construct the tree, particularly for documents containing multiple tags?
- consolidated_1a30bff6ccc7941967da: concern_narrowed / requires_human_review; Could the conclusion clarify that the demonstrated usefulness of averaged fastText features concerns the evaluated supervised classifiers, and identify the evidence or intended qualification for its contrast with averaged unsupervised word2vec vectors?
  - 待检查：Does the complete paper contain a comparison of averaged word2vec vectors and fastText features using the same downstream classifier, evaluation datasets, embedding dimensions and comparable training-data conditions, with frozen versus task-adapted embeddings distinguished?; How much of any comparative advantage comes from label supervision and joint classifier training, rather than the learned features or averaging operation?; Do matched word-only comparisons support the word2vec contrast independently of n-gram features? The supplied within-fastText ablations address n-gram contributions but do not isolate a difference from word2vec.
- consolidated_7065266f221bc62de86a: inconclusive / requires_human_review; Does the complete paper provide measurements of visited nodes or runtime across class counts, rather than only the large-tag benchmark?
  - 待检查：Does the complete paper contain visited-node counts or prediction-runtime measurements across class counts? The supplied excerpts establish one large-output benchmark, but cannot establish the absence of other measurements from the complete PDF.; Within the stated depth-first traversal, how are children ordered, how is the pruning threshold initialized and updated, and what stopping and tie-handling rules are used? Does the implementation return exact top-T predictions under the hierarchical model?; Does O(log(T)) describe one heap update, overhead on each visited node or candidate leaf, or another quantity? What is total prediction cost in terms of visited internal nodes, candidate leaves, representation dimension h, and requested output count T?
- consolidated_9a9de1c8e6d1e38a43f6: inconclusive / requires_human_review; Do the complete experimental sections or implementation documentation specify the CPU model and the operations included in each training and test timer?
  - 待检查：What CPU model and processor configuration were used for sentiment and tag-prediction timing, and did both tag-prediction models run on identical hardware?; For each training and test timer, were preprocessing, vocabulary creation, feature hashing, Huffman-tree construction, data loading, initialization, and model loading included or excluded?; Does each fastText training time in Table 5 cover all five epochs, and does its test time cover the complete 543,424-example test set?
- consolidated_bdd3cb2a8bdb123b2f43: inconclusive / requires_human_review; Does the complete paper distinguish end-to-end system efficiency from speed attributable to particular algorithmic components?
  - 待检查：For the Vowpal Wabbit comparison, were datasets, features, hidden dimensions, loss and inference procedures, epochs, hardware, thread counts, and achieved accuracy matched, and which timing boundaries produced the reported 2–5 times difference?; Is there a same-model thread-scaling comparison that quantifies the contribution of parallelism separately from implementation and algorithm choices?; Is hierarchical softmax or tree-based inference compared with exhaustive scoring within the same fastText implementation and model configuration, especially on YFCC100M?
- consolidated_23e6a7c956e176a37f5c: inconclusive / requires_human_review; Does the original description or dataset script compute the frequency threshold over the whole corpus or only the training partition?
  - 待检查：Are word and tag frequencies counted over the full corpus, a preliminary eligible subset, or only the training partition?; Does filtering remove only word and tag types, or also examples, and how are examples with no retained tags handled?; Is precision at one scored against all original tags or only retained tags, and which examples enter its denominator?
- consolidated_4b0110cc826e3be3b84e: inconclusive / requires_human_review; Do the complete model and tag-prediction sections specify how multiple tags are converted into training targets?
  - 待检查：For YFCC100M training, is one retained tag sampled per record, is each record expanded into separate tag targets, or is a loss computed over several tags? What normalization or weighting is used?; Are records with no tags remaining after frequency filtering discarded, retained without updates, or handled otherwise in each split?; Does precision at one count a prediction as correct when it matches any retained reference tag, and which records enter its denominator?
- 35 项解析覆盖疑点保留在冻结来源中，需按相关原文逐项复核。
- 7 项表格结构限制保留在冻结来源中。
- 2 项图像或图注限制保留在冻结来源中。
- The supplied quality state identifies ambiguous reading order and incomplete section mapping. A human-readable reference document and reliable human page references are unavailable, preventing verification against the original document.
- The loss and hierarchical probability equations are fragmented in the supplied extraction; their exact transcription remains unverified.
- Results prose refers to Figure 1 and Figure 3 while supplied result captions identify tables; these object references cannot be reconciled from the supplied evidence.
- Source quality: reading_order_ambiguous
- Source quality: section_mapping_incomplete
- Source quality: human_reading_reference_missing
- Source quality: human_page_reference_missing
