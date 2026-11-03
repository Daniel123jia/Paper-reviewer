from backend.app import select_evidence


def test_equation_question_includes_formula_and_preceding_fragment():
    blocks = [
        {"id": "lead", "page_index": 1, "excerpt": "z_i ="},
        {"id": "formula", "page_index": 1, "excerpt": "sum_j alpha_ij (x_j W + a_ij)\n(3)"},
        {"id": "prose", "page_index": 1, "excerpt": "Relation-aware self-attention is described in this section."},
    ]
    selected = select_evidence(blocks, "Explain equation (3) in relation-aware self-attention")
    assert [row["id"] for row in selected[:2]] == ["lead", "formula"]
