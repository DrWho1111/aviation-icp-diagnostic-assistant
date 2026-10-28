import asyncio
import pytest
from icp.coverage_boundaries import asks_equipment_requirements,asks_history_proof
from icp.tools import Tools
from icp.knowledge import Knowledge
from icp.engine import format_records
from icp.answering import validate_grounded


@pytest.mark.parametrize('question',['列出设备的正式需求','查看系统需求条目','请提供SystemRequirement节点'])
def test_missing_device_requirements_do_not_search_paper_examples(monkeypatch,question):
    monkeypatch.setenv('RAGFLOW_API_KEY','test-placeholder-never-used')
    result=asyncio.run(Tools(Knowledge()).execute('Hybrid_Search',{'query':question,'corpus':'research'}))
    assert result['status']=='not_covered' and result['evidence']==[]
    assert '未提供' in format_records('Hybrid_Search',result)


def test_requirement_theory_is_not_a_missing_device_requirement():
    assert not asks_equipment_requirements('什么是系统需求工程')
    assert not asks_equipment_requirements('如何编写正式系统需求')


def test_historical_proof_explanation_does_not_pick_a_current_cause():
    question='能否用历史记录确认这次故障原因？'
    assert asks_history_proof(question)
    result=asyncio.run(Tools(Knowledge()).execute('Fault_History_Query',{'query':question}))
    answer=format_records('Fault_History_Query',result)
    assert '不能直接证明' in answer and '现场' in answer
    assert result['data']==[] and result['coverage']['view']=='policy'


def test_valid_citation_does_not_authorize_a_current_incident_cause():
    evidence=[{'id':'DOC-006-T1-R2'}]
    value={'answer':'本次故障的根因是存储介质退化。','citations':['DOC-006-T1-R2'],'sufficient':True}
    with pytest.raises(ValueError):validate_grounded(value,evidence)


def test_unknown_module_english_expansion_requires_source_support():
    value={'answer':'ICU（Input Conditioning Unit）是输入调理模块。','citations':['source'],'sufficient':True}
    with pytest.raises(ValueError):validate_grounded(value,[{'id':'source','text':'ICU 输入调理模块'}])
    assert validate_grounded(value,[{'id':'source','text':'ICU: Input Conditioning Unit'}])['sufficient']
