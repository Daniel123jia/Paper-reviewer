# DRAFT — NOT APPROVED

This version has not received final reviewer approval.

# Review Comments for Authors

## General Assessment

The authors propose incorporating relative position representations directly into Transformer self-attention and report improvements in machine translation. The clarification below concerns the interpretation of generalization to unseen sequence lengths: the architectural rationale explains how the representations can remain applicable, whereas maintained translation performance beyond the training-length range requires distinct empirical support.

## Points Requiring Clarification

### 1. Clarify the meaning and evidence for generalization to unseen sequence lengths

In the discussion of relative position clipping, the authors state that clipping “enables the model to generalize to sequence lengths not seen during training.” Please clarify whether this denotes the applicability of the representation scheme at unseen lengths or maintained translation performance at those lengths. The authors’ explanation that relative position representations are independent of total sequence length provides a rationale for architectural applicability. However, the described clipping-distance ablation reports aggregate development-set BLEU as the clipping distance varies; it does not isolate performance on sequences beyond the training-length range. This distinction matters because readers may interpret the statement as evidence of empirical length extrapolation. If architectural applicability is intended, please state that scope explicitly. If maintained translation performance is intended, please identify the relevant training-length boundary and the evaluation beyond it, and indicate where the supporting results are reported.

Paper locations: PDF page 2; PDF page 3; PDF page 4; PDF page 5.
