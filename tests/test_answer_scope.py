import asyncio

import pytest

from icp.answer_scope import general_explanation
from icp.engine import Engine
from icp.knowledge import Knowledge
from icp.retrieval_boundaries import definition_term,term_in_sources
from icp.storage import Store
from icp.tools import Tools


@pytest.mark.parametrize('question',[
    '航空icp是什么意思，一般的话会出现什么严重的问题',
    '什么是向量数据库，打个生活中的比方',
    '操作系统是什么意思？','航空ICP一般会出现哪些常见问题？',
])
def test_general_explanations_are_not_limited_to_local_document_definitions(question):
    assert general_explanation(question,Knowledge())


@pytest.mark.parametrize('mode',['auto','general'])
@pytest.mark.parametrize('question',[
    '根据研究资料解释功能架构并引用来源',
    '查询HMC 91810001-**-0001的含义',
    '计算RBD-SYN-001的10小时可靠度',
    '我这次的画面卡住了，怎么排查？',
    '我们这个系统所有HMC有哪些？',
    '查询PFL清单','提供正式设备系统需求',
    '历史案例是否证明本次根因？',
])
def test_general_mode_cannot_invent_equipment_records_or_bypass_diagnostic_confirmation(question,mode):
    assert not general_explanation(question,Knowledge(),mode)


def test_known_module_and_explicit_knowledge_mode_keep_equipment_context():
    kb=Knowledge()
    assert not general_explanation('ICU是什么',kb)
    assert not general_explanation('通道处理器是干什么',kb)
    assert not general_explanation('什么是向量数据库',kb,'knowledge')
    assert general_explanation('解释操作系统的进程和线程',kb,'general')


@pytest.mark.parametrize('question',['航空icp是什么意思','航电 ICP 是什么','航空电子ICP是什么'])
def test_domain_qualified_icp_is_the_same_subject(question):
    term=definition_term(question)
    assert term=='ICP'
    assert term_in_sources(term,[{'raw_text':'综合核心处理机（ICP）承担多种计算任务。'}])
    assert not term_in_sources(term,[{'raw_text':'一种不相关的传感器。'}])


def test_general_answer_actually_calls_model_and_has_no_fabricated_sources(monkeypatch):
    import icp.engine as module
    calls=[]
    async def model(question,facts,**kwargs):
        calls.append((question,facts,kwargs))
        return '航空ICP指综合核心处理机。一般可能出现计算、数据或通信异常，具体影响取决于系统设计。'
    monkeypatch.setattr(module,'generate',model)
    engine=Engine(Tools(Knowledge()),Store('sqlite:///:memory:'))
    run=engine.new('航空icp是什么意思，一般的话会出现什么严重的问题','general-educational')
    asyncio.run(engine.process(run))
    assert run['status']=='completed' and run['answer_basis']=='general_model'
    assert run['generation']=='llm' and calls[0][2]=={'general':True,'background':True}
    assert not run['trace'] and not run['evidence'] and '通用知识说明' in run['answer']


def test_general_mode_still_respects_configured_sensitive_rule(monkeypatch):
    async def forbidden(*args,**kwargs):raise AssertionError('Sensitive gate must run before the model')
    monkeypatch.setattr('icp.engine.generate',forbidden)
    engine=Engine(Tools(Knowledge()),Store('sqlite:///:memory:'))
    run=engine.new('诊断演示敏感词是什么','sensitive-general','general')
    asyncio.run(engine.process(run))
    assert run['status']=='completed' and '敏感规则' in run['answer'] and run.get('answer_basis')!='general_model'


def test_knowledge_mode_does_not_fall_back_to_general_chat_for_nonbusiness_terms(monkeypatch):
    async def forbidden(*args,**kwargs):raise AssertionError('Knowledge-only mode cannot call general chat')
    monkeypatch.setattr('icp.engine.generate',forbidden)
    monkeypatch.setattr('icp.engine.planner.enabled',lambda:False)
    engine=Engine(Tools(Knowledge()),Store('sqlite:///:memory:'))
    run=engine.new('什么是二分查找？','knowledge-only-term','knowledge')
    asyncio.run(engine.process(run))
    assert run['status']=='completed' and run.get('answer_basis')!='general_model'
    assert any(t['tool']=='Hybrid_Search' for t in run['trace'])


@pytest.mark.parametrize('question',['ICP是什么','综合处理机主要做什么','解释系统工程中的功能架构','解释RBD的含义'])
def test_domain_definitions_default_to_sources_but_allow_explicit_general_explanation(question):
    kb=Knowledge()
    assert not general_explanation(question,kb,'auto')
    assert general_explanation(question,kb,'general')
    assert general_explanation('请通俗解释，'+question,kb,'auto')
