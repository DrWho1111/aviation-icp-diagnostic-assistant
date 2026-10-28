import pytest
from scripts.graph_model_profile import no_thinking_template


def test_graph_template_enforces_no_think_without_a_provider_flag():
    original='{{- if and $.IsThinkSet (eq $i $lastUserIdx) }}\n{{- if $.Think -}}\n/think\n{{ else }}/no_think{{ end }}\n{{ if and $.IsThinkSet (not $.Think) -}}<think>\n\n</think>{{ end }}'
    result=no_thinking_template(original)
    assert '{{- if eq $i $lastUserIdx }}' in result
    assert '{{- if false -}}' in result
    assert '{{ if and $.IsThinkSet (not $.Think) -}}<think>' in result
    with pytest.raises(ValueError):no_thinking_template('different upstream template')
