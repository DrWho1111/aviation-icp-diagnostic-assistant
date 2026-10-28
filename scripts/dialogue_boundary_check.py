"""Additional development regressions; do not alter the frozen 120 questions."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import time
import uuid
from zoneinfo import ZoneInfo
import httpx
from model_integration import wait,snapshot

ROOT=Path(__file__).resolve().parents[1]
CASES=[
 ('colloquial','画面卡住但后台数据还在更新，咋排查？','needs_confirmation','Fault_Reasoning_Engine'),
 ('typo','画面卡主了但后台数据在更新，怎么排查？','needs_confirmation','Fault_Reasoning_Engine'),
 ('lowercase','hmc 91810001-**-0001 啥意思，咋维修？','completed','HMC_Query'),
 ('multiple_codes','分别查询HMC 91810001-**-0001和91810001-**-0002的维修方法',None,'HMC_Query'),
 ('linked_records','FM-SYN-001对应哪些MFL维修清单？','completed','MFL_PFL_Query'),
 ('nonfinite_time','计算RBD-SYN-001的NaN小时可靠度','needs_input',None),
 ('unmodeled_equipment','发动机燃油泵漏油，应该怎么排查？','completed','Hybrid_Search'),
 ('unbound_pronoun','它有什么影响？','needs_input',None),
]


def main(base):
 report={'date':datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),'base_url':base,
     'scope':'additional exposed development regression; not frozen or blind accuracy','cases':[]}
 path=ROOT/'outputs/dialogue-boundary-check.json'
 with httpx.Client(base_url=base,trust_env=False,timeout=30) as client:
  for cid,question,status,tool in CASES:
   started=time.monotonic();row={'id':cid,'question':question,'passed':False}
   try:
    rid=client.post('/api/chat',json={'question':question,'session_id':'dialogue-'+uuid.uuid4().hex}).raise_for_status().json()['run_id']
    run=wait(client,rid);checks={}
    if status:checks['status']=run['status']==status
    if tool:checks['tool']=any(t['tool']==tool for t in run['trace'])
    if cid=='multiple_codes':checks['no_silent_partial_answer']=run['status']=='needs_input' or (run['status']=='completed' and all(code in run['answer'] for code in ['91810001-**-0001','91810001-**-0002']))
    if cid=='unmodeled_equipment':checks['no_forced_candidates']=not run['candidates'] and '没有' in run['answer'] and not any(t.get('branches') for t in run['trace'])
    if cid=='unbound_pronoun':checks['no_tool_for_unknown_reference']=not run['trace']
    if cid in {'colloquial','typo'}:checks['expected_candidate']='FUNC-410' in {c['id'] for c in run['candidates']}
    row.update(checks=checks,run=snapshot(run),passed=all(checks.values()))
   except Exception as exc:row['error_type']=type(exc).__name__
   row['seconds']=round(time.monotonic()-started,3);report['cases'].append(row)
   path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
   print(json.dumps({k:row[k] for k in ['id','passed','seconds']}),flush=True)
 report.update(passed=all(c['passed'] for c in report['cases']),complete=len(report['cases'])==len(CASES))
 path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
 return report['passed']


if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--base-url',default='http://127.0.0.1:8002')
 raise SystemExit(not main(p.parse_args().base_url))
