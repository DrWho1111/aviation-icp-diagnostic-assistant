"""Real native-loop regression including paused restart, confirmation binding and parameter resume."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import shutil
import subprocess
import time
import uuid
from zoneinfo import ZoneInfo
import httpx
from model_integration import wait

ROOT=Path(__file__).resolve().parents[1]
DOCKER=shutil.which('docker') or r'D:\Apps\Docker\resources\bin\docker.exe'
CONTAINER='icp-final-validation-app-1'


def private_state(rid,container=CONTAINER,public=None):
    assert re.fullmatch('[0-9a-f]{32}',rid)
    # Only workflow hashes and a containment boolean leave the server. The
    # platform workflow ID/form credential never enter stdout or the report.
    code="import sys,json,hashlib;from icp.storage import Store;p=json.load(sys.stdin);w=Store().workflow("+repr(rid)+") or {};wid=w.get('workflow_id','');token=w.get('form_token','');assert wid;print(json.dumps({'workflow_sha256':hashlib.sha256(wid.encode()).hexdigest(),'form_token_absent_from_public':bool(token) and p.get('public') is not None and token not in p['public']}))"
    result=subprocess.run([DOCKER,'exec','-i',container,'python','-c',code],input=json.dumps({'public':public}),capture_output=True,text=True,encoding='utf-8',check=True)
    return json.loads(result.stdout)


def main(base_url='http://127.0.0.1:8002',container=CONTAINER,output='native-loop-end-to-end.json'):
    if not re.fullmatch(r'icp-(?:workbench|final-validation|clean-workbench-final\d+)-app-1',container):raise ValueError('Choose a named ICP application container')
    target=(ROOT/'outputs'/output).resolve()
    if not target.is_relative_to((ROOT/'outputs').resolve()):raise ValueError('Output must remain under outputs')
    checks=[];report={'date':datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),'base_url':base_url,'container':container,
        'scope':'actual Dify loop and application restart against the explicitly named local application','checks':checks}
    with httpx.Client(base_url=base_url,trust_env=False,timeout=30) as client:
        def ask(q,sid):return wait(client,client.post('/api/chat',json={'question':q,'session_id':sid}).raise_for_status().json()['run_id'])
        def check(name,passed):
            checks.append({'name':name,'passed':bool(passed)})
            target.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps(checks[-1]),flush=True)
        sid='native-restart-'+uuid.uuid4().hex
        run=ask('画面卡住但后台数据在更新，怎么排查？',sid)
        check('native_pause_before_branches',run['status']=='needs_confirmation' and not any(t.get('branches') for t in run['trace']))
        if run['status']!='needs_confirmation':report['failed_run']=run;raise RuntimeError('Not paused')
        wid=private_state(run['id'],container)['workflow_sha256']
        public=json.dumps(run)+client.get('/api/runs/'+run['id']+'/trace').text
        check('form_token_not_in_public_snapshot_or_trace',private_state(run['id'],container,public)['form_token_absent_from_public'])
        subprocess.run([DOCKER,'restart',container],check=True,capture_output=True)
        for _ in range(60):
            try:
                if client.get('/api/health').status_code==200:break
            except httpx.HTTPError:pass
            time.sleep(.5)
        restored=client.get('/api/runs/'+run['id']).raise_for_status().json()
        check('paused_state_survives_real_restart',restored['status']=='needs_confirmation' and private_state(run['id'],container)['workflow_sha256']==wid)
        payload={'function_id':'FUNC-410','session_id':sid,'corpus_version':run['corpus_version'],'confirmation_token':run['confirmation_token']}
        bad=client.post('/api/runs/'+run['id']+'/confirm',json={**payload,'function_id':'FUNC-999'})
        check('wrong_candidate_rejected_before_platform_submission',bad.status_code==409)
        client.post('/api/runs/'+run['id']+'/confirm',json=payload).raise_for_status()
        final=wait(client,run['id'])
        check('same_native_run_completed_after_confirmation',final['status']=='completed' and private_state(run['id'],container)['workflow_sha256']==wid)
        check('four_branches_and_uncertainty',any(len(t.get('branches',[]))==4 for t in final['trace']) and '尚未确认本次根因' in final['answer'])
        check('duplicate_confirmation_is_idempotent',client.post('/api/runs/'+run['id']+'/confirm',json=payload).raise_for_status().json()['reused_confirmation'])
        session='native-time-'+uuid.uuid4().hex
        missing=ask('计算RBD-SYN-001可靠度',session)
        check('missing_time_native_pause',missing['status']=='needs_input' and not missing['trace'])
        if missing['status']!='needs_input':raise RuntimeError('No parameter pause')
        before=private_state(missing['id'],container)['workflow_sha256']
        supplied=ask('那10小时呢',session)
        check('parameter_reply_resumes_same_native_run',supplied['status']=='completed' and '0.99951007' in supplied['answer'] and private_state(supplied['id'],container)['workflow_sha256']==before)
        session='native-hmc-'+uuid.uuid4().hex
        missing=ask('查询HMC 91810001','native-hmc-'+session)
        check('tool_discovered_missing_code_native_pause',missing['status']=='needs_input')
        if missing['status']!='needs_input':raise RuntimeError('No HMC parameter pause')
        before=private_state(missing['id'],container)['workflow_sha256']
        supplied=ask('完整编号是91810001-**-0001','native-hmc-'+session)
        check('hmc_reply_resumes_same_native_run',supplied['status']=='completed' and '91810001-**-0001' in supplied['answer'] and private_state(supplied['id'],container)['workflow_sha256']==before)
        report.update(passed=all(c['passed'] for c in checks),corpus_version=run['corpus_version'])
        target.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        return report['passed']


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--base-url',default='http://127.0.0.1:8002');parser.add_argument('--container',default=CONTAINER);parser.add_argument('--output',default='native-loop-end-to-end.json')
    args=parser.parse_args()
    try:raise SystemExit(not main(args.base_url,args.container,args.output))
    except Exception as exc:print('Native end-to-end stopped; error type='+type(exc).__name__);raise SystemExit(1)
