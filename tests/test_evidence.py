import pytest

from backend.evidence import interpret_evidence

SOURCE = {'id': 'S1', 'title': 'Apple product report', 'content': 'Apple announced a new product.',
          'coverage': 'Company event'}
SELECTION = {'source_id': 'S1', 'topic': 'product', 'evidence_quote': SOURCE['content']}


def test_model_prose_is_never_displayed_even_with_valid_evidence():
    result = interpret_evidence({**SELECTION, 'possible_influence': 'An acquisition definitely caused a huge rally.'}, SOURCE)
    assert result['evidence_quote'] == SOURCE['content']
    assert 'acquisition' not in result['possible_influence']
    assert 'definitely' not in result['possible_influence']
    assert result['interpretation_method'] == 'evidence-selected-template'


@pytest.mark.parametrize('changes', [
    {'topic': 'legal'}, {'topic': ['product']}, {'topic': 'unknown'},
    {'evidence_quote': None}, {'evidence_quote': 'Apple announced an acquisition.'},
    {'evidence_quote': 'product'}, {'evidence_quote': 'x' * 301},
])
def test_invalid_or_mismatched_evidence_is_rejected(changes):
    assert interpret_evidence({**SELECTION, **changes}, SOURCE) is None


def test_quote_must_come_from_excerpt_not_title_or_other_source():
    assert interpret_evidence(SELECTION, {**SOURCE, 'title': SOURCE['content'], 'content': ''}) is None
    assert interpret_evidence(SELECTION, {**SOURCE, 'id': 'S2', 'content': 'Microsoft announced a new product.'}) is None


def test_denials_and_opinion_are_not_rewritten_as_confirmed_events():
    source = {**SOURCE, 'content': 'Apple denied the acquisition rumor.', 'coverage': 'Opinion / comparison'}
    result = interpret_evidence({'topic': 'deal', 'evidence_quote': source['content']}, source)
    assert result['evidence_quote'] == source['content']
    assert 'opinion' in result['possible_influence']
    assert 'denial' in result['possible_influence']


def test_holdings_are_rejected_even_with_valid_quote_and_topic():
    assert interpret_evidence(SELECTION, {**SOURCE, 'coverage': 'Holdings report'}) is None
