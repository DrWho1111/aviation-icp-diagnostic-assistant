from icp.retrieval_boundaries import navigation_only,definition_term,term_in_sources
import asyncio
import pytest


def test_navigation_pages_do_not_support_body_facts():
    assert navigation_only('目录\n功能介绍..............2\n软件设计.............3\n结论.............7')
    assert not navigation_only('输入调理模块完成限幅、滤波与欠压检测。')


def test_definition_guard_checks_the_requested_concept_not_generic_shared_words():
    term=definition_term('请解释系统工程中的功能分解方法')
    assert term=='功能分解方法'
    assert not term_in_sources(term,[{'raw_text':'某测试系统的用户界面设计方法'}])
    assert term_in_sources('RBD',[{'raw_text':'可靠性框图描述成功逻辑'}])
    assert not term_in_sources('RBD',[{'raw_text':'关联模型：RBD-SYN-004；某历史故障维修记录。'}])
    assert not term_in_sources('RBD',[{'raw_text':'关联文档索引：RBD→RBD-SYN-004《显示输出功能可靠性模型》'}])
    assert term_in_sources('RBD',[{'raw_text':'RBD用于表示系统成功逻辑。'}])
    assert definition_term('画面卡住但后台数据在更新，怎么排查') is None
    assert definition_term('请问ICP是什么')=='ICP'
    assert definition_term('请介绍一下ICU的作用')=='ICU'
    assert definition_term('什么是可靠性框图')=='可靠性框图'


def test_function_question_uses_the_named_subject_without_weakening_concept_guards():
    evidence=[{'raw_text':'FUNC-100 | 电源调理与分配 | 为系统各模块提供稳定、可监测的电源。'}]
    for question in ['电源调理与分配的功能是什么？','请介绍一下电源调理与分配的功能','请问电源调理与分配的作用是什么？']:
        term=definition_term(question)
        assert term=='电源调理与分配'
        assert term_in_sources(term,evidence)
    assert definition_term('显示画面合成的功能是什么？')=='显示画面合成'
    assert not term_in_sources(definition_term('显示画面合成的功能是什么？'),evidence)
    assert definition_term('系统工程中的功能架构是什么？')=='功能架构'
    assert not term_in_sources('功能架构',evidence)


def test_named_subject_architecture_question_keeps_subject_support_required():
    evidence=[{'raw_text':'综合核心处理机（ICP）通过通用功能模块和交换网络实现共享与重构。'}]
    for question in ['ICP的综合处理架构是什么','ICP的体系结构是什么？','请介绍一下ICP的组成']:
        assert definition_term(question)=='ICP'
        assert term_in_sources(definition_term(question),evidence)
    assert definition_term('不存在设备的内部结构是什么')=='不存在设备'
    assert not term_in_sources(definition_term('不存在设备的内部结构是什么'),evidence)
    assert definition_term('系统工程中的功能架构是什么')=='功能架构'
    assert not term_in_sources('功能架构',evidence)


@pytest.mark.parametrize('question',['综合核心处理机的模块化设计和资源共享有哪些特点？','FTA和RBD有什么区别？'])
def test_explicit_research_queries_cannot_be_intercepted_by_device_catalog_or_comparison(monkeypatch,question):
    from icp.knowledge import Knowledge
    from icp.tools import Tools
    kb=Knowledge();source=kb.data['evidence']['DOC-012-S358'];calls=[]
    monkeypatch.setenv('RAGFLOW_API_KEY','test-local-research-key')
    monkeypatch.setenv('RAGFLOW_RESEARCH_DATASET_IDS','test-research-dataset')
    async def retrieve(query,datasets,limit):
        calls.append((query,datasets))
        return {'chunks':[{'content':f"[CORPUS_VERSION={kb.version}] [SOURCE_ID={source['id']}] 研究资料正文"}]}
    monkeypatch.setattr('icp.tools.ragflow_search',retrieve)
    result=asyncio.run(Tools(kb).execute('Hybrid_Search',{'query':question,'corpus':'research'}))
    assert calls==[(question,['test-research-dataset'])]
    assert result['status']=='ok'
    assert result['coverage']['retrieval']=='ragflow_hybrid'
    assert result['evidence'] and all(e['corpus']=='research' for e in result['evidence'])
