"""Measure actual retrieval with frozen, single-primary-record relevance labels."""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from dotenv import dotenv_values

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from icp.integrations import ragflow_search
from icp.knowledge import Knowledge


async def main(split):
    source=ROOT/'eval/extended_cases.json';digest=hashlib.sha256(source.read_bytes()).hexdigest()
    if digest!=(ROOT/'eval/extended_cases.sha256').read_text().strip():raise RuntimeError('Frozen cases changed')
    suite=json.loads(source.read_text(encoding='utf-8'));kb=Knowledge()
    if suite['source_version']!=kb.version:raise RuntimeError('Frozen source version differs')
    config=dotenv_values(ROOT/'.env');validation=dotenv_values(ROOT/'.secrets/validation.env')
    for key in ['RAGFLOW_BASE_URL','RAGFLOW_API_KEY','RAGFLOW_RERANK_ID','RAGFLOW_RERANK_THRESHOLD']:
        if config.get(key):os.environ[key]=config[key]
    rows=[]
    report={'date':'2026-10-24','corpus_version':kb.version,'frozen_case_sha256':digest,'split':split,'cases':rows,
        'label_policy':'one primary source record per question; Recall@6 equals primary-record Hit@6, not exhaustive multi-evidence recall',
        'independence':'source-derived labels; evaluation occurs after some acceptance failures were exposed; not a second blind holdout',
        'target_recall_at6':.9}
    output=ROOT/'outputs/retrieval-acceptance.json'
    for case in suite['cases']:
        if case['split']!=split:continue
        expected=case.get('expected_candidate_id')
        if not expected:
            match=re.search(r'\d{8}-(?:\*\*|\d{2})-\d{4}|(?:FM|FTA|RBD|MFL|HIS)-SYN-\d{3}',case['question'])
            expected=match[0] if match else None
        if expected not in kb.records or not case.get('expected_source_ids'):continue
        function=kb.records[expected]['kind']=='function'
        variable='RAGFLOW_ENTITY_DATASET_IDS' if function else 'RAGFLOW_SYNTHETIC_DATASET_IDS'
        ids=[s for s in validation[variable].split(',') if s]
        kwargs={'document_ids':validation['RAGFLOW_FUNCTION_DOCUMENT_IDS'].split(','),'similarity_threshold':0.0,'vector_similarity_weight':.8} if function else {}
        query=('子功能 ' if function else '')+case['question']
        row={'case':case['id'],'question':case['question'],'expected_record':expected,'passed':False}
        try:
            result=await ragflow_search(query,ids,30 if function else 6,**kwargs)
            records=[]
            for chunk in result.get('chunks',[]):
                content=chunk.get('content','')
                if f'[CORPUS_VERSION={kb.version}]' not in content:continue
                match=re.search(r'\[RECORD_ID=([^\]]+)\]',content)
                if match and match[1] in kb.records and match[1] not in records:records.append(match[1])
            raw=records[:6]
            if function:records=[rid for rid in records if kb.records[rid]['kind']=='function' and kb.records[rid]['fields']['parent_id']] or records
            row.update(raw_top6=raw,final_top6=records[:6],passed=expected in records[:6],raw_hit_at6=expected in raw)
        except Exception as exc:row['error_type']=type(exc).__name__
        rows.append(row)
        report['summary']={'executed':len(rows),'hit':sum(r['passed'] for r in rows),
            'primary_record_recall_at6':sum(r['passed'] for r in rows)/len(rows),'unique_gold_records':len({r['expected_record'] for r in rows})}
        output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'case':row['case'],'passed':row['passed']}),flush=True)
    report['complete']=True;report['target_met']=report['summary']['primary_record_recall_at6']>=.9
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--split',choices=['development','acceptance'],default='acceptance')
    asyncio.run(main(parser.parse_args().split))
