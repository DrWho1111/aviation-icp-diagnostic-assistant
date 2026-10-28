"""Verify reviewed page supplements are retrievable with exact physical-page provenance."""
import json
from pathlib import Path
import httpx
from dotenv import dotenv_values

ROOT=Path(__file__).resolve().parents[1]
CASES=[('DOC-022',87,'图5-12 因果网络构建模式 网络层次 网络节点 节点可靠性 因果强度'),
 ('DOC-024',7,'Abstract G1000 FTA BDD RETE Eclipse CLIPS Android SQLite'),
 ('DOC-024',79,'buttonListener4 setText Y N getResourceString DiagnosisActivity FunctionChoose'),
 ('DOC-024',80,'createRootDirectoryIfDoesNotExist MEDIA_MOUNTED FileNotFoundException realpath'),
 ('DOC-024',84,'createRestartTask createNextTask createPreviousTask ExpertTask implements Runnable'),
 ('DOC-024',85,'class NextTask extends ExpertTask stateId es.next PreviousTask es.previous')]


def main():
    env=dotenv_values(ROOT/'.env');checks=[]
    with httpx.Client(base_url='http://127.0.0.1:8002',headers={'Authorization':'Bearer '+env['INTERNAL_TOOL_TOKEN']},timeout=90,trust_env=False) as client:
        for sid,page,query in CASES:
            outer=client.post('/internal/tools/Hybrid_Search',json={'input':json.dumps({'query':query,'corpus':'research'},ensure_ascii=False)}).raise_for_status().json()
            result=json.loads(outer['result']);locations=[{'document_id':e['document_id'],'page':e['page'],'source_id':e['id']} for e in result.get('evidence',[])]
            check={'document_id':sid,'page':page,'query':query,'locations':locations,
                'passed':result['status']=='ok' and any(e['document_id']==sid and e['page']==page for e in result['evidence'])}
            checks.append(check);print(json.dumps({'document_id':sid,'page':page,'passed':check['passed']}),flush=True)
    report={'date':'2026-10-24','scope':'source-derived physical-page regression, not independent Recall@6','checks':checks,'passed':all(c['passed'] for c in checks)}
    (ROOT/'outputs/ocr-retrieval-verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':main()
