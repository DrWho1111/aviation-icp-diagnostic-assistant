import asyncio
import json

import pytest

from icp.answering import readable_text,validate_grounded
from icp.engine import Engine,format_records,route
from icp.knowledge import Knowledge
from icp.query_intent import catalog_kind,constrain_action
from icp.storage import Store
from icp.tools import Tools


@pytest.mark.parametrize('question',['有哪些硬件模块','模块清单能介绍一下吗','帮我看看模块目录'])
def test_module_catalog_questions_stay_in_domain(question):
    assert route(question)=='Hybrid_Search'


@pytest.mark.parametrize('question',['hmc都有什么呢','HMC有哪些','健康管理码一共有几条','介绍一下HMC','故障代码是怎么分段的'])
def test_hmc_open_questions_are_catalogs(question):
    kb=Knowledge();result=asyncio.run(Tools(kb).execute('HMC_Query',{'query':question}))
    assert result['status']=='ok' and result['coverage']['total']==100
    assert sum(g['count'] for g in result['coverage']['groups'])==100
    text=format_records('HMC_Query',result)
    assert '100' in text and 'LCN' in text and 'HAMC' in text
    assert 'CORPUS_VERSION' not in text


def test_hmc_subsystem_and_module_filters_are_real():
    tool=Tools(Knowledge())
    power=asyncio.run(tool.execute('HMC_Query',{'query':'电源部分有哪些HMC'}))
    assert power['coverage']['matched']==30 and power['coverage']['total']==100
    module=asyncio.run(tool.execute('HMC_Query',{'query':'ICU的HMC有哪些'}))
    assert 0<module['coverage']['matched']<30
    assert all(r['name'].startswith('ICU') for r in module['data'])
    absent=asyncio.run(tool.execute('HMC_Query',{'query':'发动机有哪些HMC'}))
    assert absent['status']=='not_found' and absent['coverage']['matched']==0


@pytest.mark.parametrize('question,kind,count',[('有哪些子系统','subsystem',6),('系统的功能清单有哪些','function',30),('全部FTA模型有哪些','fta',8),('MFL清单一共多少条','mfl',72)])
def test_catalogs_come_from_data_not_fixed_answers(question,kind,count):
    tool=Tools(Knowledge());result=asyncio.run(tool.execute('Hybrid_Search',{'query':question}))
    assert result['coverage']['kind']==kind and result['coverage']['total']==count
    if kind=='mfl':assert sum(g['count'] for g in result['coverage']['groups'])==count and len(result['coverage']['groups'])==6


@pytest.mark.parametrize('question',['HMC 91810001','查询91810001-**-0001','A通道处理器挂起有什么影响','计算RBD-SYN-001可靠度'])
def test_open_question_support_does_not_replace_specific_queries(question):
    assert catalog_kind(question) is None


def test_business_overview_cannot_be_redirected_to_research():
    action=constrain_action('hmc都有什么呢',{'tool':'Hybrid_Search','query':'HMC','corpus':'research','missing_fields':['故障码']})
    assert action['tool']=='HMC_Query' and action['corpus']=='synthetic' and not action['missing_fields']
    assert constrain_action('HMC有哪些',{'tool':'End'})['tool']=='End'


def test_index_metadata_and_json_are_never_rendered_as_prose():
    raw='[CORPUS_VERSION=version] [RECORD_ID=record] [SOURCE_ID=source]\nfile.pdf '+json.dumps({'content':'功能架构\n描述功能关系。'},ensure_ascii=False)+'\n[SOURCE_METADATA=[{"id":"source"}]]'
    assert readable_text(raw)=='功能架构\n描述功能关系。'


def test_citation_validation_accepts_any_used_source_not_only_first():
    evidence=[{'id':'DOC-001-S1'},{'id':'DOC-002-S2'}]
    result=validate_grounded({'answer':'根据功能表可作这样的说明。','citations':['DOC-002-S2'],'sufficient':True},evidence)
    assert 'DOC-002-S2' in result['answer']
    with pytest.raises(ValueError):validate_grounded({'answer':'伪造来源','citations':['DOC-999-S9'],'sufficient':True},evidence)
    with pytest.raises(ValueError):validate_grounded({'answer':'[CORPUS_VERSION=secret]','citations':['DOC-001-S1'],'sufficient':True},evidence)
    with pytest.raises(ValueError):validate_grounded({'answer':'HMC（硬件维护手册）','citations':['DOC-001-S1'],'sufficient':True},evidence)


def test_failed_model_summary_does_not_dump_index_contents(monkeypatch):
    import icp.engine as module
    from icp.integrations import IntegrationError
    monkeypatch.delenv('TOOL_BACKEND',raising=False)
    monkeypatch.delenv('RAGFLOW_API_KEY',raising=False)
    monkeypatch.setattr(module.planner,'enabled',lambda:False)
    calls=[]
    async def broken(*args):
        calls.append(1)
        raise IntegrationError('No valid citation')
    monkeypatch.setattr(module,'generate_grounded',broken)
    engine=Engine(Tools(Knowledge()),Store('sqlite:///:memory:'))
    run=engine.new('输入限幅是什么，请依据系统资料解释','open-question-test')
    asyncio.run(engine.process(run))
    assert run['status']=='completed'
    assert run.get('generation')=='evidence_fallback'
    assert len(calls)==1
    assert 'CORPUS_VERSION' not in run['answer'] and 'SOURCE_METADATA' not in run['answer']
