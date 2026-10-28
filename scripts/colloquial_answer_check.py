"""Check conversational answers against the actual app, Dify and retrieval services."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import time
import uuid
from zoneinfo import ZoneInfo

import httpx

ROOT=Path(__file__).resolve().parents[1]


def main():
    cases=[
        ('compound','我们这个系统是干什么的，解释一下通道处理器是干什么', ['关于本工作台','A','B','冗余']),
        ('colloquial','通道处理器有什么用？',['A','B','冗余']),
        ('application','这个工作台能做什么？',['故障','来源','确认']),
        ('absent','不存在设备的处理器是干什么？',[]),
    ]
    report={'date':datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),'mode':'real_current_dify_ragflow_qwen',
            'scope':'Four targeted regression examples, not an independent accuracy or complete acceptance result.',
            'cases':[],'passed':False}
    with httpx.Client(base_url='http://127.0.0.1:8000',timeout=30,trust_env=False) as c:
        for attempt in range(60):
            try:
                report['health']=c.get('/api/health').raise_for_status().json()
                break
            except httpx.HTTPError:
                if attempt==59:raise
                time.sleep(1)
        for cid,question,expected in cases:
            owner='colloquial-check-'+uuid.uuid4().hex;session='colloquial-'+uuid.uuid4().hex
            start=time.monotonic();observed=[];interim_export=None
            rid=c.post('/api/chat',json={'question':question,'client_id':owner,'session_id':session}).raise_for_status().json()['run_id']
            while time.monotonic()-start<240:
                run=c.get('/api/runs/'+rid).raise_for_status().json()
                if not observed or observed[-1]!=run['answer']:observed.append(run['answer'])
                if run['status'] not in {'queued','running'}:break
                if run['trace'] and interim_export is None:
                    interim_export=c.get('/api/conversations/'+session+'/export',params={'client_id':owner}).raise_for_status().text
                time.sleep(.5)
            exported=c.get('/api/conversations/'+session+'/export',params={'client_id':owner}).raise_for_status().text
            answer=run['answer']
            checks={'completed':run['status']=='completed','key_facts':all(text in answer for text in expected),
                    'no_index_dump':not any(text in answer for text in ['CORPUS_VERSION=','[RECORD_ID=','第53 卷','Computer Knowledge and Technology']),
                    'export_matches_final_answer':answer in exported,
                    'interim_export_has_no_provisional_answer':interim_export is None or '尚未形成最终答复' in interim_export}
            if cid in {'compound','colloquial'}:
                checks['module_sources']={'DOC-004-T5-R8','DOC-004-T5-R9'}.issubset({e['id'] for e in run['evidence']})
                checks['not_falsely_rejected']='暂不将相关段落拼成定义' not in answer
                checks['actual_dify_tool']=any(t['tool']=='Hybrid_Search' and t['backend']=='dify' for t in run['trace'])
                checks['no_unsubstantiated_switching_guarantee']=not any(text in answer for text in ['确保','保证','系统会自动切换'])
            if cid=='absent':checks['unsupported_subject_is_not_invented']=run.get('generation')=='insufficient_evidence' or '未找到' in answer or '未覆盖' in answer
            item={'id':cid,'question':question,'run_id':rid,'status':run['status'],'generation':run.get('generation'),
                  'answer':answer,'evidence_ids':[e['id'] for e in run['evidence']],
                  'trace':[{'tool':t['tool'],'status':t['status'],'corpus':t['params'].get('corpus'),'elapsed_ms':t['elapsed_ms']} for t in run['trace']],
                  'elapsed_seconds':round(time.monotonic()-start,3),'checks':checks,'passed':all(checks.values())}
            report['cases'].append(item)
            (ROOT/'outputs/colloquial-answer-live-check.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps({'case':cid,'passed':item['passed'],'status':run['status'],'generation':run.get('generation'),
                              'failed_checks':[k for k,v in checks.items() if not v]},ensure_ascii=False),flush=True)
    report['source_sha256']={p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (ROOT/'backend/icp').glob('*.py')}
    report['passed']=all(item['passed'] for item in report['cases'])
    (ROOT/'outputs/colloquial-answer-live-check.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    if not report['passed']:raise SystemExit(1)


if __name__=='__main__':main()
