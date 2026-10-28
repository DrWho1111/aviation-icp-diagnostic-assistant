"""Run frozen cases against the real application; preserve failures and separate semantic review."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import time
import uuid
import re
import sys
import httpx
from model_integration import wait,snapshot

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from icp.coverage_boundaries import asks_equipment_requirements,asks_history_proof
from icp.answering import validate_grounded
from icp.retrieval_boundaries import definition_term,term_in_sources


def resume_report(path,digest,split,only_ids,cases,version):
    value=json.loads(path.read_text(encoding='utf-8'))
    if (value.get('case_sha256')!=digest or value.get('split')!=split or value.get('case_filter','')!=only_ids
            or value.get('corpus_version')!=version):raise ValueError('Resume checkpoint scope/version differs')
    rows=value.get('cases',[])
    if [r['id'] for r in rows]!=[c['id'] for c in cases[:len(rows)]]:raise ValueError('Resume case order differs')
    if any(r.get('question')!=c['question'] for r,c in zip(rows,cases)):raise ValueError('Resume questions differ')
    return value


def main(base,split,only_ids='',resume_from=None):
    source=ROOT/'eval/extended_cases.json';digest=hashlib.sha256(source.read_bytes()).hexdigest()
    if digest!=(ROOT/'eval/extended_cases.sha256').read_text().strip():raise RuntimeError('Frozen cases changed')
    suite=json.loads(source.read_text(encoding='utf-8'));cases=[c for c in suite['cases'] if split=='all' or c['split']==split]
    if only_ids:cases=[c for c in cases if c['id'] in only_ids.split(',')]
    if not cases:raise ValueError('No selected cases')
    output=ROOT/'outputs'/('extended-eval-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.json')
    report={'date':datetime.now().isoformat(),'case_sha256':digest,'split':split,'case_filter':only_ids,'cases':[],
        'independence':suite['independence'],'semantic_fact_accuracy':'requires human review; not inferred from valid citation IDs',
        'scope':'actual HTTP/Dify/RAGFlow evaluation; source-derived test questions, not independent human blind evaluation'}
    if resume_from:
        output=Path(resume_from).resolve()
        if not output.is_relative_to((ROOT/'outputs').resolve()):raise ValueError('Resume checkpoint must be an output report')
        report=resume_report(output,digest,split,only_ids,cases,suite['source_version'])
        if report.get('base_url',base)!=base:raise ValueError('Resume only against the same application endpoint')
        report.setdefault('resume_events',[]).append({'date':datetime.now().isoformat(),'completed_cases_retained':len(report['cases']),
            'reason':'continue remaining cases with background output redirected; frozen questions and existing results retained'})
    knowledge=json.loads((ROOT/'data/processed/knowledge.json').read_text(encoding='utf-8'))
    report['base_url']=base
    report['automated_checks_scope']='status, tool, source identity/content and selected material/evidence boundary invariants; not complete answer semantic accuracy'
    # Polling transport timeout is separate from the task/performance targets.
    # Preserve slow timings instead of abandoning a live task during a read.
    report['http_read_timeout_seconds']=60
    with httpx.Client(base_url=base,trust_env=False,timeout=httpx.Timeout(60,connect=10)) as client:
        deadline=time.monotonic()+60
        while True:
            try:
                health=client.get('/api/health').raise_for_status().json();break
            except httpx.HTTPError:
                if time.monotonic()>=deadline:raise RuntimeError('Application did not become ready') from None
                time.sleep(.5)
        if health['corpus_version']!=suite['source_version']:raise RuntimeError('Application and frozen source version differ')
        report['corpus_version']=health['corpus_version']
        for case in cases[len(report['cases']):]:
            start=time.monotonic();row={'id':case['id'],'category':case['category'],'question':case['question'],'passed':False}
            try:
                sid='frozen-'+uuid.uuid4().hex
                rid=client.post('/api/chat',json={'question':case['question'],'session_id':sid}).raise_for_status().json()['run_id']
                run=wait(client,rid);traces=run.get('trace',[]);evidence=run.get('evidence',[]);answer=run.get('answer','')
                checks={'status':run['status']==case['expected_status'],'readable':bool(answer) and all(x not in answer for x in ['[CORPUS_VERSION=','<think>','[RECORD_ID='])}
                checks['source_snapshot_matches_current_version']=all(e.get('id') in knowledge['evidence'] and e==knowledge['evidence'][e['id']] for e in evidence)
                if run.get('generation')=='llm' and evidence:
                    try:validate_grounded({'answer':answer,'citations':[e['id'] for e in evidence],'sufficient':True},evidence)
                    except ValueError:checks['answer_semantic_guards']=False
                    else:checks['answer_semantic_guards']=True
                    term=definition_term(case['question'])
                    if term:checks['definition_term_has_source_support']=term_in_sources(term,evidence)
                if case['expected_tool']:checks['tool']=any(t['tool']==case['expected_tool'] and t.get('backend')=='dify' for t in traces)
                checks['no_forbidden_tool']=not any(t['tool'] in case['forbidden_tools'] for t in traces)
                if case['required_answer_fragments']:checks['answer_fragments']=all(x in answer for x in case['required_answer_fragments'])
                if case['expected_source_ids']:checks['source_overlap']=bool(set(case['expected_source_ids'])&{e['id'] for e in evidence})
                if case.get('expected_candidate_id'):
                    result=run.get('result') or {}
                    candidates=run.get('candidates',[]) or (result.get('data',[]) if isinstance(result.get('data'),list) else [])
                    checks['top3']=case['expected_candidate_id'] in [r.get('id') for r in candidates[:3]]
                if case['requires_clarification']:checks['no_speculative_success']=run['status']=='needs_input'
                if case['category']=='diagnosis':
                    checks['no_branches_before_confirmation']=not any(t.get('branches') for t in traces)
                if asks_equipment_requirements(case['question']):
                    checks['missing_equipment_requirements_not_replaced_by_papers']='未提供' in answer and not any(e.get('corpus')=='research' for e in evidence)
                if asks_history_proof(case['question']):
                    checks['historical_case_is_not_current_root_cause']='不能直接证明' in answer and not re.search(r'(?:本次|此次|这次|当前)(?:故障)?的?(?:根因|原因)\s*(?:是|为|已确认|已确定)',answer)
                if re.search(r'(?<![A-Za-z])PFL(?![A-Za-z])',case['question'],re.I):
                    checks['missing_pfl_not_fabricated']='未提供' in answer and not re.search(r'PFL-SYN-\d+',answer)
                row.update(checks=checks,passed=all(checks.values()),run=snapshot(run),requires_semantic_review=True)
            except (httpx.HTTPError,TimeoutError,KeyError,TypeError,ValueError) as exc:row['error_type']=type(exc).__name__
            row['seconds']=round(time.monotonic()-start,3);report['cases'].append(row)
            report['summary']={'total':len(cases),'executed':len(report['cases']),'passed':sum(x['passed'] for x in report['cases'])}
            report['complete']=len(report['cases'])==len(cases)
            output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps({k:row[k] for k in ['id','passed','seconds']}),flush=True)
            if row.get('run',{}).get('status')=='error' and not row.get('run',{}).get('trace'):
                print('Platform execution failed before tool observation; stopping this batch and retaining results.',flush=True)
                break
    report['passed']=report['complete'] and all(c['passed'] for c in report['cases'])
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report['passed']


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--base-url',default='http://127.0.0.1:8000');p.add_argument('--split',choices=['development','acceptance','all'],default='acceptance');p.add_argument('--only-ids',default='');p.add_argument('--resume-from',type=Path)
    args=p.parse_args();raise SystemExit(not main(args.base_url,args.split,args.only_ids,args.resume_from))
