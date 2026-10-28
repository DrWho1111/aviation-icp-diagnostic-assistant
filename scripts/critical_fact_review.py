"""Check source-backed key fields in actual answers; not an exhaustive prose judge."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[1]


def main(report_path):
    suite_path=ROOT/'eval/extended_cases.json';suite=json.loads(suite_path.read_text(encoding='utf-8'))
    digest=hashlib.sha256(suite_path.read_bytes()).hexdigest()
    if digest!=(ROOT/'eval/extended_cases.sha256').read_text().strip():raise ValueError('Frozen cases changed')
    actual=json.loads(report_path.read_text(encoding='utf-8'));knowledge=json.loads((ROOT/'data/processed/knowledge.json').read_text(encoding='utf-8'))
    if actual.get('corpus_version')!=knowledge['version']:raise ValueError('Answer version differs')
    runs={r['id']:r for r in actual['cases']};records={r['id']:r for r in knowledge['records']};checks=[]
    def add(case,field,value,source_ids,answer):
        normalize=lambda s:re.sub(r'\s+','',str(s))
        valid=bool(source_ids) and all(e in knowledge['evidence'] for e in source_ids)
        checks.append({'case':case,'field':field,'expected':value,'source_ids':source_ids,
            'supported_and_answered':valid and normalize(value) in normalize(answer)})
    for case in suite['cases']:
        row=runs.get(case['id'])
        if row is None:continue
        run=row.get('run',{});answer=run.get('answer','')
        match=re.search(r'\d{8}-(?:\*\*|\d{2})-\d{4}|(?:FM|MFL|HIS|FTA|RBD)-SYN-\d{3}',case['question'])
        record=records.get(match[0]) if match else None
        if not record or case['category'] not in {'codes','effects','history','rbd','fta'}:continue
        fields=record['fields'];sources=record['evidence_ids'];kind=record['kind']
        required={'hmc':['maintenance'],'mfl':['maintenance','validation_status'],
            'fmeca':['cause','stage','local_effect','final_effect','severity','probability_class','detection','mitigation'],
            'history':['confirmed_cause','solution']}.get(kind,[])
        add(case['id'],'record_id',record['id'],sources,answer)
        for field in required:add(case['id'],field,fields[field],sources,answer)
        if kind=='rbd':
            hours=float(re.search(r'(\d+)\s*小时',case['question'])[1])
            expected=next(row[1] for row in fields['expected'][1:] if float(row[0])==hours)
            add(case['id'],'reliability_from_original_reference',expected,sources,answer)
            add(case['id'],'expression',fields['expression'],sources,answer)
            add(case['id'],'assumptions',fields['assumption'],sources,answer)
        if kind=='fta':
            add(case['id'],'probability_from_original_reference',f"{fields['source_probability']:.3e}",sources,answer)
            add(case['id'],'minimal_cut_count',f"{len(fields['minimal_cuts'])}组",sources,answer)
    if not checks:raise ValueError('No source-backed answer fields checked')
    passed=sum(c['supported_and_answered'] for c in checks)
    report={'date':datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),'corpus_version':knowledge['version'],'frozen_case_sha256':digest,
        'answer_report':report_path.relative_to(ROOT).as_posix(),'answer_report_sha256':hashlib.sha256(report_path.read_bytes()).hexdigest(),
        'checks':checks,'summary':{'cases':len({c['case'] for c in checks}),'fields':len(checks),'supported_and_answered':passed,'ratio':passed/len(checks)},
        'target':.95,'target_met_for_checked_fields':passed/len(checks)>=.95,
        'scope':'source-backed critical fields and answer completeness for structured cases; extra generated claims and general concept answers require separate semantic review',
        'independence':'source-derived reference fields, after first acceptance exposure; not an external blind factuality judgment',
        'not_claimed':'This field ratio is not an exhaustive all-sentence factuality rate.'}
    (ROOT/'outputs/critical-fact-review.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report['summary']))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--input',type=Path,required=True)
    args=p.parse_args();main(args.input.resolve())
