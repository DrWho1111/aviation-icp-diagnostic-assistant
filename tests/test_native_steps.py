import asyncio
import pytest
from icp.engine import Engine
from icp.knowledge import Knowledge
from icp.tools import Tools
from icp.storage import Store
from icp import planner
from icp.react_steps import operate
from icp.integrations import IntegrationError


def test_native_controller_plan_and_execute_are_separate_single_rounds(monkeypatch):
    for key in ['TOOL_BACKEND','RAGFLOW_API_KEY','LLM_MODEL']:monkeypatch.delenv(key,raising=False)
    async def choose(q,obs,user):
        return {'tool':'End' if obs else 'HMC_Query','query':'91810001-**-0001','corpus':'synthetic','missing_fields':[],'summary':'test'}
    monkeypatch.setattr(planner,'choose',choose)
    engine=Engine(Tools(Knowledge()),Store('sqlite:///:memory:'));run=engine.new('查询HMC 91810001-**-0001','native-step')
    payload={'root_run_id':run['id']}
    async def check():
        first=await operate(engine,'plan',payload)
        assert first['kind']=='execute' and not engine.store.get(run['id'])['trace']
        await operate(engine,'execute',payload)
        assert len(engine.store.get(run['id'])['trace'])==1
        final=await operate(engine,'plan',payload)
        saved=engine.store.get(run['id'])
        assert final['kind']=='done' and saved['status']=='completed'
        assert [t['tool'] for t in saved['trace']]==['HMC_Query','End']
    asyncio.run(check())


def test_native_plan_pauses_for_negative_time_without_calling_tool(monkeypatch):
    async def choose(*args):return {'tool':'RBD_Query','query':'计算RBD-SYN-001的负1小时可靠度','corpus':'synthetic','missing_fields':[],'summary':'test'}
    monkeypatch.setattr(planner,'choose',choose)
    engine=Engine(Tools(Knowledge()),Store('sqlite:///:memory:'));run=engine.new('计算RBD-SYN-001的负1小时可靠度','native-step')
    result=asyncio.run(operate(engine,'plan',{'root_run_id':run['id']}))
    assert result['kind']=='input' and not engine.store.get(run['id'])['trace']
    assert engine.store.get(run['id'])['status']=='running'


def test_native_observation_cannot_bypass_confirmation():
    engine=Engine(Tools(Knowledge()),Store('sqlite:///:memory:'));run=engine.new('画面卡住怎么排查','native-step')
    run['native_pending_action']={'tool':'Fault_Reasoning_Engine','params':{'query':run['question']}};engine.store.save(run)
    with pytest.raises(IntegrationError):asyncio.run(operate(engine,'observe',{'root_run_id':run['id'],'result':'{}'}))


def test_native_retry_reuses_pending_plan_and_completed_action(monkeypatch):
    for key in ['TOOL_BACKEND','RAGFLOW_API_KEY','LLM_MODEL']:monkeypatch.delenv(key,raising=False)
    choices=[]
    async def choose(*args):
        choices.append(1)
        return {'tool':'HMC_Query','query':'91810001-**-0001','corpus':'synthetic','missing_fields':[],'summary':'test'}
    monkeypatch.setattr(planner,'choose',choose)
    engine=Engine(Tools(Knowledge()),Store('sqlite:///:memory:'));run=engine.new('查询HMC 91810001-**-0001','native-retry')
    async def check():
        payload={'root_run_id':run['id']}
        first=await operate(engine,'plan',payload)
        second=await operate(engine,'plan',payload)
        assert first==second and len(choices)==1
        action={**payload,'action_id':first['action_id']}
        response=await operate(engine,'execute',action)
        # A lost HTTP response must not cause a second tool execution, even
        # when the next request loads the persisted state again.
        assert await operate(engine,'execute',action)==response
        saved=engine.store.get(run['id'])
        assert len(saved['trace'])==len(saved['observations'])==1
        with pytest.raises(IntegrationError):
            await operate(engine,'execute',{**payload,'action_id':'stale-action'})
    asyncio.run(check())


def test_unknown_complete_code_ends_instead_of_asking_for_the_same_code(monkeypatch):
    for key in ['TOOL_BACKEND','RAGFLOW_API_KEY','LLM_MODEL']:monkeypatch.delenv(key,raising=False)
    async def choose(q,obs,user):
        return {'tool':'HMC_Query','query':q,'corpus':'synthetic','missing_fields':['HMC编号'] if obs else [],'summary':'test'}
    monkeypatch.setattr(planner,'choose',choose)
    engine=Engine(Tools(Knowledge()),Store('sqlite:///:memory:'));run=engine.new('查询HMC 99999999-01-9999','unknown-code')
    async def check():
        payload={'root_run_id':run['id']}
        await operate(engine,'plan',payload);await operate(engine,'execute',payload)
        final=await operate(engine,'plan',payload);saved=engine.store.get(run['id'])
        assert final['kind']=='done' and saved['status']=='completed'
        assert '未找到' in saved['answer']
        assert [t['tool'] for t in saved['trace']]==['HMC_Query','End']
    asyncio.run(check())


def test_tool_discovered_missing_hmc_creates_native_parameter_pause(monkeypatch):
    async def choose(*args):
        return {'tool':'HMC_Query','query':'91810001','corpus':'synthetic','missing_fields':[],'summary':'test'}
    monkeypatch.setattr(planner,'choose',choose)
    engine=Engine(Tools(Knowledge()),Store('sqlite:///:memory:'))
    run=engine.new('查询HMC 91810001','hmc-resume')
    async def check():
        payload={'root_run_id':run['id']}
        await operate(engine,'plan',payload)
        result=await operate(engine,'execute',payload)
        assert result['done']=='0'
        pause=await operate(engine,'plan',payload)
        assert pause['kind']=='input' and 'HAMC' in pause['note'] and 'HSC' in pause['note']
        saved=engine.store.get(run['id'])
        saved['status']='needs_input';engine.store.save(saved)
        engine.store.save_workflow(run['id'],{'kind':'native_main','purpose':'input','phase':'paused',
            'root_id':run['id'],'user':'hmc-resume','corpus_version':run['corpus_version'],'workflow_id':'same-workflow'})
        reply=engine.new('HAMC为**，HSC为0001','hmc-resume')
        assert reply['context_source_run_id']==run['id']
        assert '91810001' in reply['effective_question'] and '0001' in reply['effective_question']
        assert reply['trace']==saved['trace']
        other=engine.new('查询HMC 91810001','other-session')
        assert 'context_source_run_id' not in other
    asyncio.run(check())


def test_native_parameter_context_does_not_override_explicit_new_question():
    engine=Engine(Tools(Knowledge()),Store('sqlite:///:memory:'))
    run=engine.new('计算RBD-SYN-001可靠度','new-topic')
    run['status']='needs_input';engine.store.save(run)
    engine.store.save_workflow(run['id'],{'kind':'native_main','purpose':'input','phase':'paused',
        'corpus_version':run['corpus_version']})
    reply=engine.new('新问题：查询HMC清单','new-topic')
    assert 'context_source_run_id' not in reply


def test_pronoun_without_a_prior_object_requires_clarification():
    engine=Engine(Tools(Knowledge()),Store('sqlite:///:memory:'))
    run=engine.new('它有什么影响？','unknown-reference')
    asyncio.run(engine.process(run))
    saved=engine.store.get(run['id'])
    assert saved['status']=='needs_input' and not saved['trace'] and '唯一' in saved['answer']


def test_native_time_reply_keeps_the_resolved_model_visible():
    engine=Engine(Tools(Knowledge()),Store('sqlite:///:memory:'))
    run=engine.new('计算RBD-SYN-001可靠度','visible-model')
    run['status']='needs_input';engine.store.save(run)
    engine.store.save_workflow(run['id'],{'kind':'native_main','purpose':'input','phase':'paused','corpus_version':run['corpus_version']})
    reply=engine.new('那10小时呢','visible-model')
    assert 'RBD-SYN-001' in reply['context_note'] and '同一原生工作流' in reply['context_note']
