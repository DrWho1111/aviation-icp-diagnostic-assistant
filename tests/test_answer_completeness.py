import asyncio
import re

import pytest

from icp.engine import Engine,format_records
from icp.knowledge import Knowledge
from icp.storage import Store
from icp.tools import Tools


@pytest.mark.parametrize('number',range(1,9))
def test_requested_fta_events_and_cutsets_are_visible_in_the_answer(number,monkeypatch):
    monkeypatch.delenv('RAGFLOW_API_KEY',raising=False)
    model_id=f'FTA-SYN-{number:03d}'
    kb=Knowledge();result=asyncio.run(Tools(kb).execute('FTA_Query',{'query':model_id+'的底事件和最小割集'}))
    answer=format_records('FTA_Query',result)
    source=kb.records[model_id]['fields']
    for event in source['events']:
        assert event['id'] in answer and event['name'] in answer
    actual={frozenset(re.findall(r'EVT-SYN-\d+-BE-\d+',line)) for line in answer.splitlines() if re.match(r'^\d+\. ',line)}
    assert actual=={frozenset(cut) for cut in source['minimal_cuts']}
    assert '不代表诊断置信度' in answer and '重叠割集不独立相加' in answer


def test_catalog_count_and_shown_items_remain_consistent_without_regeneration(monkeypatch):
    import icp.engine as module
    monkeypatch.delenv('TOOL_BACKEND',raising=False)
    monkeypatch.delenv('RAGFLOW_API_KEY',raising=False)
    monkeypatch.setattr(module.planner,'enabled',lambda:False)
    async def forbidden(*args):raise AssertionError('Exact list must not be regenerated')
    monkeypatch.setattr(module,'generate_grounded',forbidden)
    engine=Engine(Tools(Knowledge()),Store('sqlite:///:memory:'))
    run=engine.new('系统有哪些功能','catalog-completeness')
    asyncio.run(engine.process(run))
    assert run['status']=='completed' and '30' in run['answer']
    listed=[line for line in run['answer'].splitlines() if line.startswith('- FUNC-')]
    assert len(listed)==12 and '先列出 12 条示例' in run['answer']
