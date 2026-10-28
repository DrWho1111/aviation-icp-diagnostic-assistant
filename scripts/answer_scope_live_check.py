"""Actual model checks for general explanation and evidence-required requests."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import time
import uuid
from zoneinfo import ZoneInfo

import httpx

ROOT=Path(__file__).resolve().parents[1]


def wait(client,rid):
    deadline=time.monotonic()+240
    while time.monotonic()<deadline:
        run=client.get('/api/runs/'+rid).raise_for_status().json()
        if run['status'] not in {'queued','running'}:return run
        time.sleep(.4)
    raise TimeoutError('Run did not reach a terminal or paused state')


def main():
    report={'date':datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),'passed':False,'cases':[],
        'scope':'Seven real current-app examples for explanation modes and preserved core boundaries; not full independent acceptance.'}
    cases=[
        ('auto_original','航空icp是什么意思，一般的话会出现什么严重的问题','auto'),
        ('general_vector','什么是向量数据库，用生活中的例子解释一下','general'),
        ('knowledge_nonbusiness','什么是二分查找？','knowledge'),
        ('knowledge_icp','航空icp是什么意思，请根据研究资料解释并引用来源','knowledge'),
        ('general_rbd','计算RBD-SYN-001的10小时可靠度','general'),
        ('general_hmc','查询HMC 91810001-**-0001的含义和维修方法','general'),
        ('general_diagnosis','画面卡住但后台数据在更新，怎么排查？','general'),
    ]
    with httpx.Client(base_url='http://127.0.0.1:8000',timeout=30,trust_env=False) as client:
        for attempt in range(60):
            try:client.get('/api/health').raise_for_status();break
            except httpx.HTTPError:
                if attempt==59:raise
                time.sleep(1)
        for cid,question,mode in cases:
            session='scope-live-'+uuid.uuid4().hex;started=time.monotonic()
            rid=client.post('/api/chat',json={'question':question,'session_id':session,'answer_mode':mode}).raise_for_status().json()['run_id']
            run=wait(client,rid);answer=run['answer']
            checks={'requested_mode_persisted':run.get('answer_mode')==mode,'no_internal_thinking':'<think>' not in answer}
            if cid in {'auto_original','general_vector'}:
                checks.update(completed=run['status']=='completed',actual_model=run.get('generation')=='llm',
                    general_basis=run.get('answer_basis')=='general_model',no_fake_sources=not run['evidence'] and not run['trace'],
                    labelled='通用知识说明' in answer,not_false_missing='暂不将相关段落拼成定义' not in answer,
                    substantive=('综合' in answer and '处理' in answer) if cid=='auto_original' else '向量' in answer)
                if cid=='auto_original':checks['no_flight_control_overgeneralization']=(not any(t in answer for t in ['是飞机飞控系统中的关键计算机','主要控制飞机飞行']) and ('取决' in answer or '不等同' in answer))
            if cid=='knowledge_icp':
                checks.update(completed=run['status']=='completed',knowledge_basis=run.get('answer_basis')!='general_model',
                    real_dify_search=any(t['tool']=='Hybrid_Search' and t['backend']=='dify' for t in run['trace']),
                    has_sources=bool(run['evidence']),not_false_alias_rejection='暂不将相关段落拼成定义' not in answer,
                    substantive='综合' in answer and '处理' in answer)
            if cid=='knowledge_nonbusiness':
                checks.update(completed=run['status']=='completed',no_general_fallback=run.get('answer_basis')!='general_model',
                    knowledge_tool_used=any(t['tool']=='Hybrid_Search' and t['backend']=='dify' for t in run['trace']))
            if cid in {'general_rbd','general_hmc'}:
                tool='RBD_Query' if cid=='general_rbd' else 'HMC_Query'
                checks.update(completed=run['status']=='completed',general_cannot_bypass_tool=any(t['tool']==tool and t['backend']=='dify' for t in run['trace']),
                    exact_value=('0.99951007' if cid=='general_rbd' else '91810001-**-0001') in answer)
            if cid=='general_diagnosis':
                checks.update(paused_for_confirmation=run['status']=='needs_confirmation',
                    no_early_branches=not any(t.get('branches') for t in run['trace']))
                if run['status']=='needs_confirmation':
                    fid=next(c['id'] for c in run['candidates'] if c['id']=='FUNC-410')
                    client.post('/api/runs/'+rid+'/confirm',json={'function_id':fid,'confirmation_token':run['confirmation_token'],
                        'session_id':session,'corpus_version':run['corpus_version']}).raise_for_status()
                    run=wait(client,rid)
                    checks.update(confirmed_completed=run['status']=='completed',four_real_branches=any(len(t.get('branches',[]))==4 for t in run['trace']))
            item={'id':cid,'question':question,'requested_mode':mode,'status':run['status'],
                'answer_basis':run.get('answer_basis','project_evidence'),'generation':run.get('generation'),
                'answer':run['answer'],'evidence_ids':[e['id'] for e in run['evidence']],
                'tools':[{'tool':t['tool'],'backend':t['backend'],'status':t['status']} for t in run['trace']],
                'elapsed_seconds':round(time.monotonic()-started,3),'checks':checks,'passed':all(checks.values())}
            report['cases'].append(item)
            (ROOT/'outputs/answer-scope-live-check.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps({'id':cid,'passed':item['passed'],'failed':[k for k,v in checks.items() if not v]}),flush=True)
    report['source_sha256']={p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'backend/icp').glob('*.py')}
    report['passed']=all(c['passed'] for c in report['cases'])
    (ROOT/'outputs/answer-scope-live-check.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    if not report['passed']:raise SystemExit(1)


if __name__=='__main__':main()
