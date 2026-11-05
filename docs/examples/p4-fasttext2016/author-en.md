# DRAFT — NOT APPROVED

This version has not received final reviewer approval.

# Review Comments for Authors

## General Assessment

The authors present fastText as an efficient text-classification baseline that combines averaged feature embeddings, a rank-constrained linear model, hierarchical softmax, and optional n-gram features. They report competitive predictive accuracy and substantial computational advantages. The clarification below concerns the interpretation of the sentiment-classification speedup claim: the disclosed timing conditions help contextualize the comparison, but the relationship between per-epoch timings and complete training at the reported accuracies needs to be made explicit.

## Points Requiring Clarification

### 1. Clarify the calculation and training scope of the sentiment-classification speedup

In the sentiment-classification results and training-time discussion, please clarify which dataset, comparator, and model configuration support the stated speedup of at least 15,000-fold, and whether this ratio compares individual epochs or complete training runs. The authors explicitly describe the timing table as reporting single-epoch measurements, state that the reported fastText accuracies use five epochs, and disclose that the convolutional baselines run on a GPU while fastText uses a CPU with 20 threads. These disclosures are useful, but a per-epoch ratio does not by itself establish the time required to obtain the reported accuracies. Please show the calculation underlying the stated speedup, identify the baseline epoch counts or stopping criteria, and clarify whether the timing and accuracy results refer to the same configurations. If the corresponding complete-training times are available, please report their ratio with the relevant execution conditions and timing boundaries. Otherwise, explicitly limit the sentiment-classification speedup statement to single-epoch timing under the disclosed conditions. This clarification would allow readers to distinguish epoch-level computational efficiency from the time required to reach the reported predictive performance.

Paper locations: PDF page 1; PDF page 2; PDF page 3; PDF page 4; Table 2, PDF page 3.
