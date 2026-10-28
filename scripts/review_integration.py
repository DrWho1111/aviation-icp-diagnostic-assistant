"""Live regression for filtering, concepts, references and mission-time follow-ups."""
from datetime import datetime
import json
from pathlib import Path
import time
import uuid
from zoneinfo import ZoneInfo
import httpx
from model_integration import wait,snapshot

ROOT=Path(__file__).resolve().parents[1]


def main():
    report={'date':datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),'cases':[],
            'scope':'真实Dify/Qwen/RAGFlow应用的定向回归，不是独立准确率测量'}
    with httpx.Client(base_url='http://127.0.0.1:8000',timeout=20,trust_env=False) as client:
        deadline=time.monotonic()+60
        while True:
            try:
                report['corpus_version']=client.get('/api/health').raise_for_status().json()['corpus_version']
                break
            except httpx.HTTPError:
                if time.monotonic()>=deadline:raise RuntimeError('Application did not become ready') from None
                time.sleep(.5)
        session='review-followup-'+uuid.uuid4().hex
        cases=[('mfl_scope','电源部分有哪些MFL','MFL_PFL_Query',15),
               ('fmeca_scope','ICU模块有哪些失效模式','FMECA_Query',3),
               ('unknown_scope','发动机有哪些MFL','MFL_PFL_Query',0),
               ('history_id','HIS-SYN-001','Fault_History_Query',None),
               ('linked_maintenance','FM-SYN-043对应的MFL维修清单是什么','MFL_PFL_Query',None),
               ('linked_mode','MFL-SYN-043关联的失效模式是什么','FMECA_Query',None),
               ('module_concept','ICU是什么，请解释它在这个系统中的作用','Hybrid_Search',None),
               ('rbd_concept','RBD是什么，有什么作用','Hybrid_Search',None),
               ('missing_time','计算RBD-SYN-001可靠度',None,None),
               ('followup_hours','那10小时呢','RBD_Query',None),
               ('followup_minutes','改为30分钟','RBD_Query',None),
               ('negative_followup','那负10小时呢',None,None),
               ('unknown_reference','列出全部MFL','MFL_PFL_Query',72),
               ('ambiguous_followup','那它的维修措施呢',None,None)]
        for cid,question,tool,count in cases:
            start=time.monotonic();item={'id':cid,'question':question,'passed':False}
            try:
                sid=session if cid in {'missing_time','followup_hours','followup_minutes','negative_followup','unknown_reference','ambiguous_followup'} else 'review-'+uuid.uuid4().hex
                created=client.post('/api/chat',json={'question':question,'session_id':sid}).raise_for_status().json()
                run=wait(client,created['run_id']);answer=run['answer'];traces=run.get('trace',[])
                checks={'status':run['status']==('needs_input' if cid in {'missing_time','negative_followup','ambiguous_followup'} else 'completed'),
                        'readable':bool(answer) and all(x not in answer for x in ['[CORPUS_VERSION=','[RECORD_ID=','<think>','\\n'])}
                if tool:checks['tool']=any(t['tool']==tool and t.get('backend')=='dify' for t in traces)
                if count is not None:
                    coverage=next((t.get('observation',{}).get('coverage',{}) for t in traces if t['tool']==tool),{})
                    checks['matched_scope']=coverage.get('matched')==count
                if cid=='history_id':checks['exact_history']='HIS-SYN-001' in answer
                if cid=='linked_maintenance':checks['linked_record']='MFL-SYN-043' in answer
                if cid=='linked_mode':checks['linked_record']='FM-SYN-043' in answer
                if cid=='module_concept':checks.update(business_evidence=bool(run['evidence']) and all(e['corpus']=='synthetic' for e in run['evidence']),no_medical_hallucination='病房' not in answer)
                if cid=='followup_hours':checks.update(value='0.99951007' in answer,context=bool(run.get('context_source_run_id')))
                if cid=='followup_minutes':checks.update(value=run['result']['data'][0].get('hours')==.5,context=bool(run.get('context_source_run_id')))
                if cid in {'negative_followup','ambiguous_followup'}:checks['no_speculative_tool']=not traces
                item.update(passed=all(checks.values()),checks=checks,run={**snapshot(run),**{k:run.get(k) for k in ['effective_question','context_note','context_source_run_id']}})
            except (httpx.HTTPError,TimeoutError,KeyError,ValueError,TypeError) as exc:item['error_type']=type(exc).__name__
            item['seconds']=round(time.monotonic()-start,2);report['cases'].append(item)
            report['summary']={'total':len(cases),'executed':len(report['cases']),'passed':sum(c['passed'] for c in report['cases'])}
            (ROOT/'outputs/review-integration.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps({k:item[k] for k in ['id','passed','seconds']}),flush=True)
    report['passed']=all(c['passed'] for c in report['cases'])
    (ROOT/'outputs/review-integration.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report['passed']


if __name__=='__main__':raise SystemExit(not main())
