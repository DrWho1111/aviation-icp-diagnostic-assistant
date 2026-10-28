import asyncio
import pytest
from icp.tools import Tools
from icp.knowledge import Knowledge
from icp.query_intent import constrain_action


@pytest.mark.parametrize('tool,params,status',[
    ('Hybrid_Search',{'query':{'text':'系统'}},'error'),
    ('Hybrid_Search',{'query':'系统','corpus':['research']},'needs_input'),
    ('HMC_Query',{'query':''},'needs_input'),
    ('RBD_Query',{'query':'RBD-SYN-001','function_id':{}},'needs_input'),
    ('HMC_Query',{'query':'查询91810001-**-0001和91810001-01-0101'},'needs_input'),
    ('RBD_Query',{'query':'比较RBD-SYN-001与RBD-SYN-002的10小时可靠度'},'needs_input'),
])
def test_invalid_or_ambiguous_tool_input_does_not_query_or_take_only_the_first(monkeypatch,tool,params,status):
    monkeypatch.setenv('RAGFLOW_API_KEY','test-placeholder-not-used')
    result=asyncio.run(Tools(Knowledge()).execute(tool,params))
    assert result['status']==status and result['data']==[]


def test_multiple_hmc_question_can_be_decomposed_without_overwriting_the_single_action():
    question='分别查询HMC 91810001-**-0001和91810001-01-0101'
    action={'tool':'HMC_Query','query':'91810001-01-0101','corpus':'synthetic','missing_fields':[]}
    assert constrain_action(question,action)['query']=='91810001-01-0101'


@pytest.mark.parametrize('tool',['Hybrid_Search','Function_Locator','Fault_Reasoning_Engine'])
def test_explicit_unmodeled_equipment_does_not_force_three_icp_candidates(monkeypatch,tool):
    monkeypatch.setenv('RAGFLOW_API_KEY','test-placeholder-not-used')
    query='发动机燃油泵漏油，应该怎么排查？'
    result=asyncio.run(Tools(Knowledge()).execute(tool,{'query':query}))
    assert result['status']=='not_covered' and not result['data'] and '发动机' in result['coverage']['equipment_scope']
    action=constrain_action(query,{'tool':'Fault_Reasoning_Engine','query':query,'missing_fields':[],'corpus':'synthetic'})
    assert action['tool']=='Hybrid_Search'


def test_engine_payload_does_not_block_explicit_icp_target():
    from icp.coverage_boundaries import unsupported_equipment_target
    assert unsupported_equipment_target('ICP显示模块无法显示发动机数据，怎么排查？') is None
    assert unsupported_equipment_target('查询FUNC-410对发动机参数显示异常的影响') is None
    from icp.engine import route
    assert route('发动机燃油泵漏油，应该怎么排查？')=='Hybrid_Search'


def test_completed_hmc_reply_takes_priority_over_original_partial_code():
    result=asyncio.run(Tools(Knowledge()).execute('HMC_Query',{'query':'查询HMC 91810001\n用户补充：完整编号是91810001-**-0001'}))
    assert result['status']=='ok' and [r['id'] for r in result['data']]==['91810001-**-0001']


def test_colloquial_diagnostic_question_stays_in_the_domain_workflow():
    from icp.engine import route
    assert route('画面卡住但后台数据还在更新，咋排查？')=='Fault_Reasoning_Engine'
    action=constrain_action('解释ICP模块的功能',{'tool':'Fault_Reasoning_Engine','query':'ICP模块','missing_fields':[],'corpus':'synthetic'})
    assert action['tool']=='Hybrid_Search'
