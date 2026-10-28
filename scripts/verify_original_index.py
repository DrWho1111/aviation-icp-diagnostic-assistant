"""Verify every available original, keeping requirements out of device retrieval."""
import asyncio
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import sys
from zoneinfo import ZoneInfo
import httpx
from dotenv import load_dotenv,dotenv_values

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from icp.knowledge import Knowledge
from icp.integrations import ragflow_search


async def main():
    load_dotenv(ROOT/'.env');kb=Knowledge()
    path=ROOT/'.secrets/ragflow-import.json';state=json.loads(path.read_text(encoding='utf-8'))
    if state.get('version')!=kb.version:state=json.loads(path.with_name('ragflow-import-'+kb.version+'.json').read_text(encoding='utf-8'))
    if state.get('version')!=kb.version:raise ValueError('Import version differs')
    rows=[];report={'date':datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),'corpus_version':kb.version,'originals':rows,'complete':False}
    with httpx.Client(base_url=os.environ['RAGFLOW_BASE_URL']+'/api/v1',headers={'Authorization':'Bearer '+os.environ['RAGFLOW_API_KEY']},timeout=60,trust_env=False) as client:
        for source in kb.data['documents']:
            if source['status']=='link_only':continue
            sid=source['id'];did=state.get('original_documents',{}).get(sid);ds=state['datasets'].get('original-'+source['corpus'])
            row={'source_id':sid,'corpus':source['corpus'],'passed':False}
            if did and ds:
                data=client.get(f'/datasets/{ds}/documents',params={'id':did,'page_size':100}).raise_for_status().json()
                if data.get('code')!=0:raise RuntimeError('Original status query failed')
                doc=next((d for d in data.get('data',{}).get('docs',[]) if d['id']==did),{})
                row.update(status=doc.get('run'),chunks=doc.get('chunk_count',0),
                    source_sha256_matches=hashlib.sha256((ROOT/source['path']).read_bytes()).hexdigest()==source['sha256'])
                row['passed']=str(row['status']) in {'DONE','3'} and row['chunks']>0 and row['source_sha256_matches']
            rows.append(row)
    requirement_id=state['datasets'].get('original-requirements');active=set()
    for config in [dict(dotenv_values(ROOT/'.env')),dict(dotenv_values(ROOT/'.secrets/validation.env'))]:
        for key in ['RAGFLOW_DATASET_IDS','RAGFLOW_SYNTHETIC_DATASET_IDS','RAGFLOW_RESEARCH_DATASET_IDS','RAGFLOW_ENTITY_DATASET_IDS']:
            active.update(v for v in (config.get(key) or '').split(',') if v)
    isolated=bool(requirement_id) and requirement_id not in active
    retrieval_ok=False;retrieved_sources=[]
    if requirement_id and all(r['passed'] for r in rows if r['corpus']=='requirements'):
        result=await ragflow_search('Function Locator 用户确认 四路检索',[requirement_id],6,similarity_threshold=0)
        inverse={v:k for k,v in state['original_documents'].items()}
        retrieved_sources=list(dict.fromkeys(inverse.get(c.get('document_id'),'unmapped') for c in result.get('chunks',[])))
        retrieval_ok='DOC-002' in retrieved_sources
    report.update(complete=True,passed=all(r['passed'] for r in rows) and isolated and retrieval_ok,
        expected_available_originals=30,registered_sources=31,link_only_excluded=['DOC-021'],
        requirements_separate_from_active_device_queries=isolated,requirements_actual_retrieval_passed=retrieval_ok,
        requirements_retrieved_source_ids=retrieved_sources,
        summary={'originals':len(rows),'parsed':sum(r['passed'] for r in rows),'requirements':sum(r['corpus']=='requirements' and r['passed'] for r in rows)},
        scope='native parsing/index status plus a real requirements-only retrieval; this does not mean every PDF page was independently judged semantically')
    (ROOT/'outputs/original-index-verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:report[k] for k in ['passed','summary','requirements_separate_from_active_device_queries','requirements_actual_retrieval_passed']}))
    return report['passed']


if __name__=='__main__':
    try:raise SystemExit(not asyncio.run(main()))
    except Exception as exc:print('Original index verification stopped; error type='+type(exc).__name__);raise SystemExit(1)
