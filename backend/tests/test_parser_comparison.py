from pathlib import Path

from scripts.compare_parsers import evaluate_landmarks, make_single_column_pdf


def test_landmark_evaluation_detects_wrong_reading_order():
    reference = {"headings": ["2 Background", "2.2 Self-Attention", "3 Proposed Architecture"],
                 "table_rows": ["Yes Yes 25.8"], "equations": ["(3)"],
                 "figure_captions": ["Figure 1"]}
    correct = "2 Background\n2.2 Self-Attention\n3 Proposed Architecture\n| Yes | Yes | 25.8 |\n(3)\nFigure 1"
    wrong = correct.replace("2.2 Self-Attention\n3 Proposed Architecture",
                            "3 Proposed Architecture\n2.2 Self-Attention")

    assert evaluate_landmarks(correct, reference)["heading_order"] == "observed_in_order"
    assert evaluate_landmarks(wrong, reference)["heading_order"] == "out_of_order"
    assert evaluate_landmarks(correct, reference)["table_row_token_sequences_found"] == 1
    assert evaluate_landmarks(correct, reference)["structured_table_rows_found"] == 0
    assert evaluate_landmarks(correct, reference, ["Yes Yes 25.8"])["structured_table_rows_found"] == 1


def test_second_layout_fixture_is_a_real_single_column_pdf(tmp_path):
    path = tmp_path / "single-column.pdf"
    make_single_column_pdf(path)

    import fitz
    with fitz.open(path) as pdf:
        assert pdf.page_count == 2
        first = pdf[0].get_text()
        assert "1 Introduction" in first
        assert "Table 1" in first
        assert "Figure 1" in first
        assert "E = mc2 (1)" in first
        assert "References" in pdf[1].get_text()
