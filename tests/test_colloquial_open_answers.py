"""Regressions for conversational module questions and multiple retrieval rounds."""
import asyncio

import pytest

from icp.engine import Engine
from icp.integrations import IntegrationError
from icp.knowledge import Knowledge
from icp.query_intent import constrain_action,workspace_question
from icp.retrieval_boundaries import definition_term,term_in_sources
from icp.storage import Store
from icp.tools import Tools
from icp.answering import validate_grounded


@pytest.mark.parametrize('question',[
    '通道处理器是干什么','通道处理器是干什么的？','解释一下通道处理器是干什么',
    '通道处理器做什么','通道处理器有什么用','请问通道处理器负责什么',
    '我们这个系统是干什么的，解释一下通道处理器是干什么',
])
def test_colloquial_subject_is_not_the_entire_question(question):
    term=definition_term(question)
    assert term=='通道处理器'
    kb=Knowledge()
    assert term_in_sources(term,kb.evidences(['DOC-006-T5-R5','DOC-006-T5-R8']))
    assert not term_in_sources(term,[{'raw_text':'光纤通道网络采用FC协议传输数据。'}])


def test_application_help_does_not_interpret_an_unspecified_equipment_as_the_app():
    assert workspace_question('我们这个系统是干什么的，解释一下通道处理器是干什么')==(True,'解释一下通道处理器是干什么')
    assert workspace_question('这个工作台有什么功能？')==(True,'')
    assert workspace_question('ICP系统有什么功能')[0] is False
    assert workspace_question('系统是干什么的')[0] is False


def test_known_module_questions_use_equipment_sources_without_hijacking_research():
    kb=Knowledge();action={'tool':'Hybrid_Search','query':'ICP处理器架构','corpus':'research','missing_fields':[]}
    q='我们这个系统是干什么的，解释一下通道处理器是干什么'
    fixed=constrain_action(q,action,kb)
    assert fixed['query']=='解释一下通道处理器是干什么' and fixed['corpus']=='synthetic'
    assert constrain_action('论文中的通道处理器是干什么',action,kb)['corpus']=='research'
    assert constrain_action('不存在设备是干什么',action,kb)['corpus']=='research'


@pytest.mark.parametrize('model_available',[True,False])
def test_multiple_hybrid_rounds_are_synthesized_even_when_last_has_no_match(monkeypatch,model_available):
    import icp.engine as module
    kb=Knowledge();engine=Engine(Tools(kb),Store('sqlite:///:memory:'))
    run=engine.new('我们这个系统是干什么的，解释一下通道处理器是干什么','compound-open')
    run.update(status='completed',evidence=kb.evidences(['DOC-012-S360','DOC-004-T5-R8','DOC-004-T5-R9','DOC-006-T5-R5','DOC-006-T5-R8']),
        result={'status':'not_found','coverage':{},'data':[]},
        observations=[{'tool':'Hybrid_Search','status':'ok','summary':'论文大段原文'},
                      {'tool':'Hybrid_Search','status':'not_found','summary':'当前未匹配'}],
        answer='论文大段原文\n当前未匹配')
    calls=[]
    async def grounded(question,evidence,summary):
        calls.append((question,evidence,summary))
        if not model_available:raise IntegrationError('model unavailable')
        return {'answer':'A通道执行主任务计算，B通道执行冗余计算并在A异常时接替。依据：DOC-004-T5-R8、DOC-004-T5-R9',
                'citations':['DOC-004-T5-R8','DOC-004-T5-R9'],'sufficient':True}
    monkeypatch.setattr(module,'generate_grounded',grounded)
    asyncio.run(engine.enrich_answer(run,'Hybrid_Search',run['question']))
    assert len(calls)==1
    assert term_in_sources('通道处理器',[calls[0][1][0]])
    assert '关于本工作台' in run['answer'] and '冗余' in run['answer']
    assert 'DOC-004-T5-R8' in run['answer'] and 'DOC-004-T5-R9' in run['answer']
    assert '论文大段原文' not in run['answer'] and '暂不将相关段落拼成定义' not in run['answer']
    assert run['generation']==('llm' if model_available else 'source_fallback')


def test_no_evidence_after_multiple_searches_never_leaks_excerpts(monkeypatch):
    engine=Engine(Tools(Knowledge()),Store('sqlite:///:memory:'))
    run=engine.new('不存在设备是什么','absent-open')
    run.update(status='completed',result={'status':'not_found','coverage':{}},
               observations=[{'tool':'Hybrid_Search'},{'tool':'Hybrid_Search'}],answer='无关原文')
    asyncio.run(engine.enrich_answer(run,'Hybrid_Search',run['question']))
    assert run['generation']=='insufficient_evidence' and '无关原文' not in run['answer']


def test_application_help_is_answered_without_device_search(monkeypatch):
    import icp.engine as module
    async def forbidden(*args):raise AssertionError('Application help needs no fabricated device lookup')
    monkeypatch.setattr(module.planner,'choose',forbidden)
    engine=Engine(Tools(Knowledge()),Store('sqlite:///:memory:'))
    run=engine.new('这个工作台能做什么？','app-help')
    asyncio.run(engine.process(run))
    assert run['status']=='completed' and run['generation']=='application_help'
    assert '并行查询' in run['answer'] and not run['trace']


def test_compound_workbench_question_is_not_an_unresolved_followup():
    engine=Engine(Tools(Knowledge()),Store('sqlite:///:memory:'))
    run=engine.new('这个工作台能做什么，通道处理器是干什么的？','compound-workbench')
    assert not run.get('context_unresolved')


@pytest.mark.parametrize('answer',['B通道确保系统持续运行。','系统会自动切换到B通道。','两个冗余通道共同确保单通道故障时仍能维持基本运行能力。'])
def test_redundancy_is_not_a_guarantee_or_an_unsourced_automation_claim(answer):
    evidence=[{'id':'DOC-004-T5-R9','text':'B通道执行冗余任务计算并在A通道异常时接替。'}]
    with pytest.raises(ValueError):
        validate_grounded({'answer':answer,'citations':['DOC-004-T5-R9'],'sufficient':True},evidence)
    validated=validate_grounded({'answer':'B通道提供冗余接替能力，不能保证系统持续运行。',
        'citations':['DOC-004-T5-R9'],'sufficient':True},evidence)
    assert validated['sufficient']


def test_native_intermediate_retrieval_is_not_published_as_final_answer(monkeypatch):
    from icp import react_steps
    kb=Knowledge();engine=Engine(Tools(kb),Store('sqlite:///:memory:'))
    run=engine.new('通道处理器是干什么','native-open')
    run['native_pending_action']={'id':'action-open','tool':'Hybrid_Search','params':{'query':run['question'],'corpus':'synthetic'}}
    engine.store.save(run)
    async def call(*args,**kwargs):
        return {'status':'ok','data':[{'content':'论文正文不应直接成为答复'}],'coverage':{},'evidence':[]}
    monkeypatch.setattr(engine,'call',call)
    asyncio.run(react_steps.operate(engine,'execute',{'root_run_id':run['id'],'action_id':'action-open'}))
    saved=engine.store.get(run['id'])
    assert saved['status']=='running' and saved['answer']=='正在核对检索证据并整理答复…'
    assert '论文正文' in saved['observations'][0]['summary']


@pytest.mark.parametrize('question,expected',[
    ('请从资料查询设备正式系统需求','未提供正式设备系统需求'),
    ('发动机故障怎么排查','没有发动机设备的诊断模型'),
])
def test_authoritative_material_gaps_are_not_replaced_with_generic_clarification(monkeypatch,question,expected):
    import icp.engine as module
    monkeypatch.setattr(module.planner,'enabled',lambda:False)
    async def forbidden(*args):raise AssertionError('Known coverage boundaries need no invented explanation')
    monkeypatch.setattr(module,'generate_grounded',forbidden)
    engine=Engine(Tools(Knowledge()),Store('sqlite:///:memory:'))
    run=engine.new(question,'explicit-material-boundary')
    asyncio.run(engine.process(run))
    assert run['status']=='completed' and not run['evidence'] and expected in run['answer']
    assert '请明确设备、模块名称或资料范围' not in run['answer']
