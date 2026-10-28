"""Verify native confirmation gating and parallel branch execution on the live platform."""
import json
from pathlib import Path
import time
import uuid
import httpx

ROOT=Path(__file__).resolve().parents[1]


def main():
    state=json.loads((ROOT/'.secrets/dify-native-diagnosis.json').read_text(encoding='utf-8'))
    report={'date':'2026-10-24','scope':'isolated native diagnosis, not production cutover','checks':[]}
    checks=report['checks'];events=[];form=None;rid=None;user='native-diagnosis-'+uuid.uuid4().hex
    with httpx.Client(base_url='http://127.0.0.1:8081/v1',headers={'Authorization':'Bearer '+state['api_key']},trust_env=False,timeout=120) as client:
        with client.stream('POST','/workflows/run',json={'inputs':{'input':json.dumps({'query':'画面卡住但后台数据在更新，怎么排查？'})},'response_mode':'streaming','user':user}) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line.startswith('data: '):continue
                v=json.loads(line[6:]);events.append({'event':v.get('event'),'node_id':v.get('data',{}).get('node_id')})
                rid=v.get('workflow_run_id') or rid
                if v.get('event')=='human_input_required':form=v;break
                if v.get('event') in {'error','workflow_finished','workflow_paused'}:break
        branch_names={'FTA_Query','FMECA_Query','RBD_Query','Fault_History_Query'}
        checks.append({'name':'paused_at_native_confirmation','passed':bool(form)})
        checks.append({'name':'no_branches_before_confirmation','passed':not any(e['node_id'] in branch_names for e in events)})
        if form:
            data=form.get('data',form);token=data.get('form_token') or form.get('form_token')
            definition=client.get('/form/human_input/'+token).raise_for_status().json()
            content=definition.get('form_content','')
            checks.append({'name':'three_candidates_with_source_ids','passed':'FUNC-410' in content and 'DOC-' in content})
            # The application must validate membership before submitting the one-shot form.
            # A form select widget is not a substitute for the backend confirmation gate.
            checks.append({'name':'candidate_membership_visible','passed':'FUNC-999' not in content and 'FUNC-410' in content})
            client.post('/form/human_input/'+token,json={'action':'approve','inputs':{'function_id':'FUNC-410'},'user':user}).raise_for_status()
            deadline=time.monotonic()+180
            while True:
                result=client.get('/workflows/run/'+rid).raise_for_status().json()
                if result.get('status') in {'succeeded','failed','stopped'} or time.monotonic()>deadline:break
                time.sleep(.5)
            succeeded=result.get('status')=='succeeded'
            checks.append({'name':'same_workflow_resumed','passed':result.get('id')==rid and succeeded})
            if succeeded:
                payload=json.loads(result['outputs']['result'])
                checks.append({'name':'four_real_results','passed':set(payload['data']['branches'])==branch_names and payload['status']=='ok'})
                checks.append({'name':'confirmed_function_and_sources','passed':payload['data']['function']['id']=='FUNC-410' and bool(payload['evidence'])})
                report['result']=payload
            else:
                report['workflow_status']=result.get('status');report['error_category']='native_workflow_failed'
                (ROOT/'.secrets/native-diagnosis-last-result.json').write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')
        report.update(events_before_confirmation=events,passed=all(c['passed'] for c in checks) and len(checks)==7)
        (ROOT/'outputs/native-diagnosis-verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'passed':report['passed'],'checks':checks},ensure_ascii=False))


if __name__=='__main__':
    try:main()
    except Exception as exc:
        print('Native diagnostic verification stopped; error type='+type(exc).__name__);raise SystemExit(1)
