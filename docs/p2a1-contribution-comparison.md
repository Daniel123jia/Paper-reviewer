# P2-A.1 contribution comparison

These are actual model-generated titles from the public-paper runs. The baseline contains repeated part-level objects. The final list uses paper-level anchors and semantic grouping. Titles reconstruct author claims; they are not independent scientific findings.

## Shaw 2018

### Before

1. **part1_c1** — Efficient relative-position self-attention
2. **part1_c2** — Relation-aware formulation
3. **part1_c3** — Translation improvements and comparison of position representations
4. **part2_c1** — Relation-aware self-attention
5. **part2_c2** — Relative position representations
6. **part3_c1** — Edge-aware self-attention
7. **part3_c2** — Learned representations of clipped relative positions
8. **part4_c1** — Efficient relative-position self-attention
9. **part4_c2** — Translation improvements from relative position representations
10. **part5_c1** — Efficient relative-position self-attention
11. **part5_c2** — General relation-aware self-attention
12. **part5_c3** — Translation improvements from relative position representations
13. **part6_c1** — Empirical evaluation of relative position representations in machine translation
14. **part7_c1** — Relative-position representations in Transformer self-attention
15. **part8_c1** — Relative-position extension to self-attention

### After

1. **contrib_1cb7039f9c6c1b35** — An efficient extension of Transformer self-attention that incorporates representations of relative positions or distances between sequence elements.
2. **contrib_fd054258b3042193** — A general relation-aware interpretation of the mechanism that can represent arbitrary pairwise input relationships and potentially support graph-labeled inputs.
3. **contrib_46f2e3164a471998** — Empirical translation improvements over absolute position representations on two WMT 2014 tasks, together with an observation that adding absolute positions to relative positions gives no further benefit.

## DistilBERT 2019

### Before

1. **part1_c1** — General-purpose language model compression through pre-training distillation
2. **part1_c2** — Triple training objective
3. **part1_c3** — Reported size, performance, and computational benefits
4. **part1_c4** — Demonstration of on-device applicability
5. **part1_c5** — Release of trained weights and training code
6. **part2_c1** — DistilBERT as a compact general-purpose language model
7. **part2_c2** — A triple training objective for distillation
8. **part3_c1** — A general-purpose language model distilled during pre-training
9. **part3_c2** — A triple training objective and analysis of training components
10. **part3_c3** — Reduced model size and inference time, including mobile deployment
11. **part3_c4** — Public availability of model and implementation resources
12. **part4_c1** — General-purpose DistilBERT through pre-training distillation
13. **part4_c2** — Ablation analysis of training losses and teacher initialization
14. **part4_c3** — Applicability of DistilBERT to edge deployment
15. **part5_c1** — General-purpose language model distilled during pre-training
16. **part5_c2** — Triple training objective
17. **part5_c3** — Release of trained weights and training code
18. **part5_c4** — Reported compression, speed, and performance retention
19. **part5_c5** — Training-component analysis and edge applicability
20. **part5_c6** — Public mobile implementation
21. **part7_c1** — General-purpose language model distilled during pre-training
22. **part7_c2** — Triple training objective
23. **part7_c3** — Release of model weights and training code
24. **part7_c4** — Ablation analysis of training components
25. **part7_c5** — Edge deployment demonstration and mobile implementation

### After

1. **contrib_1566dbcdab589253** — A general-purpose compressed language representation model obtained through distillation during pretraining, with a triple loss designed to transfer the larger model's learned inductive biases.
2. **contrib_a69a0c1a4bacd980** — Evidence of downstream utility, reduced model size and inference cost, and feasibility of on-device computation.
3. **contrib_0d14ae76d74073bd** — They claim to demonstrate successful general-purpose language model training through distillation, analyze training components through ablations, and demonstrate applicability to edge deployment.

## Manual comparison

- **Shaw:** The former 15 objects repeatedly restated relative-position attention, relation-aware interpretation and translation results. The final three groups preserve these distinct themes. Key-side and value-side mechanisms remain separate source-backed claims beneath the method contribution.
- **DistilBERT:** The former 25 objects repeated compression, triple-loss training, downstream/device results and resource availability. The final three groups retain method, empirical utility and component/ablation themes. Availability of code and a mobile implementation remains in PaperProfile, while the model classified it as ancillary to core scientific contributions.
- Grouping remains a model-assisted judgment. The server verifies index partitions, preserves original Evidence IDs, rejects foreign snapshot references and keeps every surviving claim attached to a valid contribution. A human reviewer should examine the breadth of the third DistilBERT group.
