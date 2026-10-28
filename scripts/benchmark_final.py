"""Measure live cold-model, warm single-user and three-concurrent request latency."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
import math
from pathlib import Path
import time
import uuid
import httpx

ROOT=Path(__file__).resolve().parents[1]
WORKLOADS={'structured':('查询HMC 91810001-**-0001的定义和维修方法',2),
    'knowledge':('系统工程中的功能架构是什么？请依据研究资料说明。',30),
    'diagnosis':('画面卡住但后台数据在更新，怎么排查？',90)}


def percentile(values,p):
    if not values:return None
    return sorted(values)[max(0,math.ceil(len(values)*p)-1)]


def wait(client,rid):
    deadline=time.monotonic()+300
    while time.monotonic()<deadline:
        value=client.get('/api/runs/'+rid).raise_for_status().json()
        if value['status'] not in {'queued','running'}:return value
        time.sleep(.1)
    raise TimeoutError('Benchmark task did not reach a terminal/pause state')


def sample(base,workload,phase,index):
    question,target=WORKLOADS[workload];start=time.perf_counter();human_wait=0
    row={'phase':phase,'workload':workload,'sample':index,'target_seconds':target,'passed':False}
    try:
        with httpx.Client(base_url=base,trust_env=False,timeout=30) as client:
            sid='benchmark-'+uuid.uuid4().hex
            rid=client.post('/api/chat',json={'question':question,'session_id':sid}).raise_for_status().json()['run_id']
            run=wait(client,rid)
            if workload=='diagnosis' and run['status']=='needs_confirmation':
                pause=time.perf_counter()
                if 'FUNC-410' not in {c['id'] for c in run['candidates']}:raise ValueError('Expected function was not a candidate')
                before=time.perf_counter();human_wait=before-pause
                client.post('/api/runs/'+rid+'/confirm',json={'function_id':'FUNC-410','session_id':sid,
                    'confirmation_token':run['confirmation_token'],'corpus_version':run['corpus_version']}).raise_for_status()
                run=wait(client,rid)
            elapsed=time.perf_counter()-start-human_wait
            # Timing metadata may be saved immediately after the terminal event.
            run=client.get('/api/runs/'+rid).raise_for_status().json()
            row.update(seconds=round(elapsed,4),status=run['status'],run_id=rid,corpus_version=run['corpus_version'],
                automatic_confirmation_wait_seconds=round(human_wait,4),measurements=run.get('measurements',[]),
                completed=run['status']=='completed',passed=run['status']=='completed' and elapsed<=target,
                trace=[{'tool':t['tool'],'elapsed_ms':t['elapsed_ms'],'cached':t['cached']} for t in run['trace']])
    except (httpx.HTTPError,ValueError,KeyError,TimeoutError) as exc:
        row.update(seconds=round(time.perf_counter()-start,4),error_type=type(exc).__name__,completed=False)
    print(json.dumps({k:row.get(k) for k in ['phase','workload','sample','seconds','completed','passed']}),flush=True)
    return row


def main(base,samples,cold):
    report={'date':datetime.now().isoformat(),'scope':'actual application/Dify/RAGFlow/Qwen; graph compilation and other test jobs must be idle',
        'cold_definition':'Qwen model explicitly unloaded; Docker services remain running. This is not an OS reboot or empty-platform cold boot.',
        'queue_definition':'dispatch_wait_ms is measured application scheduling wait; separate provider/model queue time is unavailable and remains null',
        'confirmation':'confirmation is automated immediately; that measured pause is subtracted and reported, not simulated as a typical human response time',
        'p95_method':'nearest rank; small sample counts are diagnostic and do not establish a production SLA','cases':[]}
    output=ROOT/'outputs/performance-final.json'
    def save():
        groups={}
        for phase in ['cold_model','warm_single','warm_concurrent_3']:
            for workload in WORKLOADS:
                cases=[r for r in report['cases'] if r['phase']==phase and r['workload']==workload]
                durations=[r['seconds'] for r in cases]
                if cases:groups[phase+'/'+workload]={'samples':len(cases),'completed':sum(r['completed'] for r in cases),
                    'p50_seconds':percentile(durations,.5),'p95_seconds':percentile(durations,.95),
                    'target_seconds':WORKLOADS[workload][1],'all_within_target':all(r['passed'] for r in cases)}
        report['summary']=groups
        output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    for workload in WORKLOADS:
        if cold:
            for index in range(3):
                with httpx.Client(trust_env=False,timeout=90) as client:
                    client.post('http://127.0.0.1:11434/api/generate',json={'model':'qwen3:8b','keep_alive':0,'stream':False}).raise_for_status()
                report['cases'].append(sample(base,workload,'cold_model',index+1));save()
        sample(base,workload,'unrecorded_warmup',0)
        for index in range(samples):report['cases'].append(sample(base,workload,'warm_single',index+1));save()
        for batch in range(max(1,math.ceil(samples/3))):
            with ThreadPoolExecutor(max_workers=3) as pool:
                rows=list(pool.map(lambda n:sample(base,workload,'warm_concurrent_3',batch*3+n),range(1,4)))
            report['cases'].extend(rows);save()
    report.update(complete=True,all_targets_met=all(r['passed'] for r in report['cases']));save()


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--base-url',default='http://127.0.0.1:8002');p.add_argument('--samples',type=int,default=6);p.add_argument('--cold',action='store_true')
    args=p.parse_args()
    if args.samples<3:raise SystemExit('At least three warm samples per workload are required')
    main(args.base_url,args.samples,args.cold)
