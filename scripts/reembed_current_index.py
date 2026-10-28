"""Refresh current-version curated vectors after correcting embedding context/batch settings."""
import json
from pathlib import Path
import httpx
from dotenv import dotenv_values

ROOT=Path(__file__).resolve().parents[1]


def main():
    kb=json.loads((ROOT/'data/processed/knowledge.json').read_text(encoding='utf-8'));version=kb['version']
    state=json.loads((ROOT/('.secrets/ragflow-import-'+version+'.json')).read_text(encoding='utf-8'))
    env=dotenv_values(ROOT/'.env');progress=ROOT/('.secrets/reembed-'+version+'.json')
    completed=json.loads(progress.read_text()) if progress.exists() else []
    marker='[EMBEDDING_PROFILE=bge-m3-cpu-ctx8192-batch8192]'
    checked=0
    with httpx.Client(base_url=env['RAGFLOW_BASE_URL']+'/api/v1',headers={'Authorization':'Bearer '+env['RAGFLOW_API_KEY']},trust_env=False,timeout=300) as client:
        for group,ds in state['datasets'].items():
            if group.startswith('original-'):continue
            docs=[state['documents'][group]]
            if group=='entity':docs.append(state['function_document_id'])
            for did in docs:
                chunks=[];page=1
                while True:
                    value=client.get(f'/datasets/{ds}/documents/{did}/chunks',params={'page':page,'page_size':100}).raise_for_status().json()
                    if value.get('code')!=0:raise RuntimeError('Chunk read failed')
                    batch=value['data']['chunks'];chunks+=batch
                    if len(batch)<100:break
                    page+=1
                for chunk in chunks:
                    checked+=1;cid=chunk['id']
                    if f'[CORPUS_VERSION={version}]' not in chunk['content']:raise RuntimeError('Foreign corpus chunk rejected')
                    if cid in completed:continue
                    content=chunk['content'] if marker in chunk['content'] else chunk['content']+'\n'+marker
                    value=client.patch(f'/datasets/{ds}/documents/{did}/chunks/{cid}',json={'content':content}).raise_for_status().json()
                    if value.get('code')!=0:raise RuntimeError('Vector refresh failed')
                    completed.append(cid);progress.write_text(json.dumps(completed),encoding='utf-8')
                    if len(completed)%100==0:print(json.dumps({'refreshed':len(completed)}),flush=True)
    expected=len(kb['chunks'])+len(kb['entity_chunks'])
    report={'date':'2026-10-24','corpus_version':version,'profile':marker,'expected':expected,'checked':checked,
            'refreshed':len(completed),'passed':len(completed)==checked==expected,'source_facts_changed':False}
    (ROOT/'outputs/embedding-index-refresh.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report))


if __name__=='__main__':
    try:main()
    except Exception as exc:print('Index refresh stopped; error type='+type(exc).__name__);raise SystemExit(1)
