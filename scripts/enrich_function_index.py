"""Update the existing 30 name chunks using source-backed behavior and expressions."""
from datetime import datetime
import json
import os
from pathlib import Path
import re
import sys
from zoneinfo import ZoneInfo
import httpx

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from icp.integrations import IntegrationError  # Loads only the local environment in memory.
from icp.knowledge import Knowledge
from icp.retrieval_profile import function_index_text, profile_digest


def main():
    kb=Knowledge();state=json.loads((ROOT/'.secrets/ragflow-import.json').read_text(encoding='utf-8'))
    if state['version']!=kb.version:raise RuntimeError('Knowledge version mismatch')
    dataset=state['datasets']['entity'];document=state['function_document_id']
    report={'date':datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),'corpus_version':kb.version,
            'profile_sha256':profile_digest(kb),'checks':[], 'scope':'原有功能名称切片补充同源行为与现象，不增加或伪造候选；不改变知识事实版本'}
    with httpx.Client(base_url=os.environ['RAGFLOW_BASE_URL'].rstrip('/')+'/api/v1',
                      headers={'Authorization':'Bearer '+os.environ['RAGFLOW_API_KEY']},timeout=120,trust_env=False) as client:
        def request(method,path,**kwargs):
            response=client.request(method,path,**kwargs)
            if not response.is_success:raise RuntimeError('RAGFlow index update HTTP failure')
            value=response.json()
            if value.get('code')!=0:raise RuntimeError('RAGFlow index update rejected')
            return value.get('data')
        path=f'/datasets/{dataset}/documents/{document}/chunks'
        chunks=request('GET',path,params={'page_size':100})['chunks']
        indexed={}
        for chunk in chunks:
            match=re.search(r'\[RECORD_ID=(FUNC-\d+)\]',chunk.get('content',''))
            if match:
                if match[1] in indexed:raise RuntimeError('Duplicate function chunks')
                indexed[match[1]]=chunk
        if set(indexed)!={r['id'] for r in kb.by_kind['function']}:raise RuntimeError('Function name inventory differs')
        for record in kb.by_kind['function']:
            chunk=indexed[record['id']];content,evidence=function_index_text(kb,record)
            if chunk['content']!=content:
                request('PATCH',path+'/'+chunk['id'],json={'content':content,
                    'important_keywords':[record['id'],record['name'],record['fields']['entity_type']],
                    'tag_kwd':['entity','synthetic',kb.version,'SubFunction' if record['fields']['parent_id'] else 'TopLevelFunction',*evidence]})
            report['checks'].append({'function':record['id'],'passed':True,'source_ids':evidence})
            (ROOT/'outputs/function-index-profile.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps({'function':record['id'],'updated':chunk['content']!=content}),flush=True)
        actual=request('GET',path,params={'page_size':100})['chunks']
        content_by_record={re.search(r'\[RECORD_ID=(FUNC-\d+)\]',c.get('content',''))[1]:c['content'] for c in actual if re.search(r'\[RECORD_ID=(FUNC-\d+)\]',c.get('content',''))}
        report['passed']=len(report['checks'])==30 and all(content_by_record.get(r['id'])==function_index_text(kb,r)[0] for r in kb.by_kind['function'])
    (ROOT/'outputs/function-index-profile.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report['passed']


if __name__=='__main__':
    try:raise SystemExit(not main())
    except Exception as exc:
        print('Function index update stopped; error type='+type(exc).__name__)
        raise SystemExit(1)
