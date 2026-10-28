"""Measure source-derived function coverage against the real RAGFlow retriever."""
import argparse
import asyncio
from datetime import datetime
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from icp.knowledge import Knowledge
from icp.tools import Tools


async def main(output):
    kb=Knowledge(); tools=Tools(kb)
    report={'date':datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(), 'corpus_version':kb.version,
            'scope':'24 leaf functions; source-derived wording; live RAGFlow hybrid and reranking',
            'limitations':['来源派生的覆盖矩阵，不是未见问题独立准确率评测；未把规则回填候选当作检索命中'], 'cases':[]}
    for function in kb.by_kind['function']:
        if not function['fields']['parent_id']: continue
        expression=function['fields'].get('expressions','').split('；')[0].strip()
        query=expression or function['name']
        result=await tools.execute('Function_Locator',{'query':query})
        ids=[r['id'] for r in result.get('data',[])]
        item={'expected':function['id'],'question':query,'retrieved':ids,'status':result['status'],
              'passed':result['status']=='ok' and function['id'] in ids,
              'real_retrieval':result.get('coverage',{}).get('retrieval')=='ragflow_entity_hybrid'}
        report['cases'].append(item)
        report['summary']={'total':24,'executed':len(report['cases']),'passed':sum(c['passed'] for c in report['cases'])}
        (ROOT/output).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(item,ensure_ascii=False),flush=True)
    return all(c['passed'] and c['real_retrieval'] for c in report['cases'])


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',default='outputs/retrieval-matrix.json')
    raise SystemExit(not asyncio.run(main(parser.parse_args().output)))
