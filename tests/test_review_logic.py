import asyncio
import pytest
from icp.engine import Engine
from icp.knowledge import Knowledge
from icp.parameters import mission_hours
from icp.query_intent import constrain_action
from icp.storage import Store
from icp.tools import Tools
from icp.followup import resolve_followup
from icp.retrieval_profile import function_index_text


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    for key in ['DIFY_AGENT_API_KEY','LLM_MODEL','TOOL_BACKEND','RAGFLOW_API_KEY']:
        monkeypatch.delenv(key,raising=False)


@pytest.mark.parametrize('text,expected',[('10小时',10),('0小时',0),('0.5小时',.5),('.5h',.5),('30分钟',.5),('2天',48),('1e2小时',100)])
def test_explicit_mission_time_units(text,expected):
    assert mission_hours(text)==expected


@pytest.mark.parametrize('text',['-10小时','负10小时','1到5小时','1-5小时','2小时或5小时','1e309小时'])
def test_time_must_not_drop_sign_or_guess_range(text):
    with pytest.raises(ValueError):mission_hours(text)


@pytest.mark.parametrize('kind,tool,question,count',[('mfl','MFL_PFL_Query','电源部分有哪些MFL',15),('fmeca','FMECA_Query','ICU模块有哪些失效模式',3),('hmc','HMC_Query','ICU有哪些HMC',6),('history','Fault_History_Query','ICU有哪些历史案例',1),('mfl','MFL_PFL_Query','发动机有哪些MFL',0),('module','Hybrid_Search','电源子系统有哪些模块',5),('function','Hybrid_Search','电源子系统有哪些功能',6),('module','Hybrid_Search','发动机有哪些模块',0),('subsystem','Hybrid_Search','给我列一下系统的全部子系统',6),('mfl','MFL_PFL_Query','请显示全部MFL',72)])
def test_catalog_scopes_use_source_associations(kind,tool,question,count):
    result=asyncio.run(Tools(Knowledge()).execute(tool,{'query':question}))
    assert result['coverage']['matched']==count
    assert result['status']==('ok' if count else 'not_found')
    assert len(result['data'])==(count if '全部' in question else min(count,12))


def test_severity_filter_does_not_include_other_levels():
    result=asyncio.run(Tools(Knowledge()).execute('FMECA_Query',{'query':'严重度为I的FMECA有哪些'}))
    assert result['data'] and all(r['fields']['severity']=='I' for r in result['data'])
    assert result['coverage']['filters']['severity']=='I'


@pytest.mark.parametrize('q,tool',[('HIS-SYN-001','Fault_History_Query'),('RBD是什么','Hybrid_Search'),('RBD是什么，有什么作用','Hybrid_Search'),('HMC和FTA有什么区别','Hybrid_Search'),('FM-SYN-043是什么阶段发生','FMECA_Query')])
def test_unambiguous_ids_and_concepts_constrain_wrong_model_action(q,tool):
    action=constrain_action(q,{'tool':'MFL_PFL_Query','query':'错误改写','corpus':'research','missing_fields':['故障码']})
    assert action['tool']==tool and action['query']==q and not action['missing_fields']


def test_followup_uses_only_latest_same_conversation(monkeypatch):
    for key in ['DIFY_AGENT_API_KEY','LLM_MODEL','TOOL_BACKEND','RAGFLOW_API_KEY']:monkeypatch.delenv(key,raising=False)
    engine=Engine(Tools(Knowledge()),Store('sqlite:///:memory:'))
    first=engine.new('计算RBD-SYN-001可靠度','followup-session');asyncio.run(engine.process(first))
    assert first['status']=='needs_input'
    other=engine.new('计算RBD-SYN-004可靠度','other-session');asyncio.run(engine.process(other))
    second=engine.new('那10小时呢','followup-session');asyncio.run(engine.process(second))
    assert second['status']=='completed' and '0.99951007' in second['answer']
    assert second['context_source_run_id']==first['id'] and second['question']=='那10小时呢'
    third=engine.new('改为30分钟','followup-session');asyncio.run(engine.process(third))
    assert third['result']['data'][0]['hours']==.5
    negative=engine.new('那-10小时呢','followup-session');asyncio.run(engine.process(negative))
    assert negative['status']=='needs_input' and not negative['trace']
    for question in ['那负10小时呢','那1到5小时呢','那1-5小时呢']:
        bad=engine.new(question,'followup-session');asyncio.run(engine.process(bad))
        assert bad['status']=='needs_input' and not bad['trace']


def test_context_references_are_not_guessed_from_multiple_records():
    kb=Knowledge();prior={'id':'prior','status':'completed','question':'列出全部MFL','result':{'data':kb.by_kind['mfl'][:3]}}
    assert resolve_followup('那它的维修措施呢',prior,kb)['context_unresolved']
    assert resolve_followup('查询HIS-SYN-002',prior,kb) is None


def test_function_enrichment_is_source_bound_and_keeps_identifier():
    kb=Knowledge();record=kb.records['FUNC-410'];content,evidence=function_index_text(kb,record)
    assert '[RECORD_ID=FUNC-410]' in content and kb.records[record['fields']['module_id']]['fields']['expressions'] in content
    assert set(evidence)<=set(kb.data['evidence']) and content.startswith('子功能：')


def test_explicit_failure_mode_maintenance_links_in_both_directions():
    kb=Knowledge();tools=Tools(kb)
    async def check():
        for record in kb.by_kind['mfl']:
            mode=record['fields']['failure_mode_ids'][0]
            forward=await tools.execute('MFL_PFL_Query',{'query':mode+'对应的MFL维修清单'})
            assert record['id'] in [r['id'] for r in forward['data']]
            backward=await tools.execute('FMECA_Query',{'query':record['id']+'关联的失效模式'})
            assert [r['id'] for r in backward['data']]==[mode]
            assert set(e['id'] for e in backward['evidence'])>=set(record['evidence_ids'])
    asyncio.run(check())
    action=constrain_action('FM-SYN-043对应的MFL维修清单是什么',{'tool':'FMECA_Query','query':'错误改写','corpus':'research','missing_fields':[]})
    assert action['tool']=='MFL_PFL_Query'
    backward=constrain_action('MFL-SYN-043关联的失效模式是什么',{'tool':'MFL_PFL_Query','query':'错误改写','corpus':'research','missing_fields':[]})
    assert backward['tool']=='FMECA_Query'


def test_failure_impact_by_explicit_function_never_returns_another_function():
    result=asyncio.run(Tools(Knowledge()).execute('FMECA_Query',{'query':'FUNC-410的失效模式及影响'}))
    assert result['status']=='ok' and len(result['data'])==3
    assert all('FUNC-410' in r['fields']['product_function'] for r in result['data'])


@pytest.mark.parametrize('time_text,expected',[('30分钟',.5),('1天',24),('负1小时',None)])
def test_time_validation_also_applies_to_later_react_actions(monkeypatch,time_text,expected):
    from icp import planner
    for key in ['TOOL_BACKEND','RAGFLOW_API_KEY','LLM_MODEL','DIFY_AGENT_API_KEY','DIFY_NATIVE_DIAGNOSIS_KEYS_FILE']:
        monkeypatch.delenv(key,raising=False)
    monkeypatch.setattr(planner,'enabled',lambda:True)
    async def choose(question,observations,user):
        if not observations:tool,q='HMC_Query','91810001-**-0001'
        elif len(observations)==1:tool,q='RBD_Query','计算RBD-SYN-001的'+time_text+'可靠度'
        else:tool,q='End','结束'
        return {'tool':tool,'query':q,'corpus':'synthetic','missing_fields':[],'summary':'检查'}
    monkeypatch.setattr(planner,'choose',choose)
    engine=Engine(Tools(Knowledge()),Store('sqlite:///:memory:'))
    run=engine.new('先查HMC 91810001-**-0001，再计算RBD-SYN-001的'+time_text+'可靠度','later-action')
    asyncio.run(engine.process(run))
    rbd=[t for t in run['trace'] if t['tool']=='RBD_Query']
    if expected is None:assert run['status']=='needs_input' and not rbd
    else:assert run['status']=='completed' and rbd[0]['observation']['data'][0]['hours']==expected
