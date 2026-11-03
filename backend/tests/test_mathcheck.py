import pytest

from backend.mathcheck import source_number_matches, verify_equation_pair


def test_correct_cross_formula_identity_has_no_false_error():
    result = verify_equation_pair('(x+y)**2', 'x**2+2*x*y+y**2')
    assert result['status'] == 'equivalent'
    assert result['counterexample'] is None


def test_known_wrong_formula_produces_reproducible_counterexample():
    first = verify_equation_pair('x+x', '3*x')
    second = verify_equation_pair('x+x', '3*x')
    assert first['status'] == 'counterexample'
    assert first['counterexample'] == second['counterexample']
    assert first['counterexample']['lhs'] != first['counterexample']['rhs']


def test_domain_assumptions_change_what_can_be_proved():
    general = verify_equation_pair('sqrt(x*x)', 'x')
    positive = verify_equation_pair('sqrt(x*x)', 'x', {'x': 'positive'})
    assert general['status'] == 'counterexample'
    assert positive['status'] == 'equivalent'


def test_real_paper_equations_4_and_5_distribute_relative_position_term():
    # Shaw et al., Self-Attention with Relative Position Representations, PDF pp. 2–3.
    source = 'q*(k+a)/sqrt(d)'
    assert verify_equation_pair(source, '(q*k+q*a)/sqrt(d)', {'d': 'positive'})['status'] == 'equivalent'
    assert verify_equation_pair(source, '(q*k+a)/sqrt(d)', {'d': 'positive'})['status'] == 'counterexample'


def test_experimental_numbers_are_checked_against_each_cited_source():
    table = 'Absolute 26.5 38.2 Relative 26.8 38.7'
    claim = 'The base model improved by 0.3 BLEU.'
    supported = source_number_matches('26.8-26.5', '0.3', table, claim)
    assert supported['all_present'] is True
    assert supported['left'] == [{'number': '26.8', 'present': True}, {'number': '26.5', 'present': True}]
    missing = source_number_matches('26.8-26.5', '1.3', table, claim)
    assert missing['all_present'] is False
    assert missing['right'] == [{'number': '1.3', 'present': False}]


@pytest.mark.parametrize('expression', ["__import__('os').system('true')", 'x.__class__', 'x[0]', 'x**1000000'])
def test_unsafe_or_unbounded_expressions_are_rejected(expression):
    with pytest.raises(ValueError):
        verify_equation_pair(expression, 'x')
