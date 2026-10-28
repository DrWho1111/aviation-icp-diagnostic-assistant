"""Run full regression, field review and latency serially after smoke checks pass."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]


def main():
 deadline=time.monotonic()+1800
 while True:
  smoke=json.loads((ROOT/'outputs/final-serial-regression.json').read_text(encoding='utf-8'))
  if smoke.get('complete') and smoke.get('passed'):break
  if any(s['exit_code'] for s in smoke['steps']):raise RuntimeError('Smoke regression failed; broad evaluation not started')
  if time.monotonic()>deadline:raise RuntimeError('Smoke regression did not complete')
  time.sleep(3)
 report={'date':'2026-10-24','scope':'post-exposure regression, field coverage and measured latency; no second blind-test claim','steps':[]}
 path=ROOT/'outputs/final-evaluation-progress.json'
 def run(script,*args):
  started=time.monotonic();result=subprocess.run([sys.executable,'scripts/'+script,*args],cwd=ROOT)
  report['steps'].append({'script':script,'exit_code':result.returncode,'seconds':round(time.monotonic()-started,2)})
  path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
  return result.returncode==0
 options=['--base-url','http://127.0.0.1:8002','--split','all']
 existing=list((ROOT/'outputs').glob('extended-eval-*.json'))
 if existing:
  latest=max(existing,key=lambda p:p.stat().st_mtime);prior=json.loads(latest.read_text(encoding='utf-8'))
  if prior.get('split')=='all' and not prior.get('complete') and prior.get('summary',{}).get('total')==120:
   options+=['--resume-from',str(latest)]
 ok=run('run_extended_eval.py',*options)
 current=max((ROOT/'outputs').glob('extended-eval-*.json'),key=lambda p:p.stat().st_mtime)
 data=json.loads(current.read_text(encoding='utf-8'))
 if data.get('split')!='all' or data.get('summary',{}).get('total')!=120:raise RuntimeError('Expected actual 120-question report')
 shutil.copy2(current,ROOT/'outputs/extended-regression-final.json')
 if not data.get('complete'):raise RuntimeError('Incomplete platform batch retained; dependent benchmarks not started')
 run('critical_fact_review.py','--input','outputs/extended-regression-final.json')
 # Measure even if an effect target fails: failures remain visible, not relabelled.
 run('benchmark_final.py','--base-url','http://127.0.0.1:8002','--samples','6','--cold')
 os.environ['ICP_APP_BASE_URL']='http://127.0.0.1:8002'
 for script in ['browser_check.py','history_browser_check.py','detail_browser_check.py','review_browser_check.py']:
  run(script)
 report.update(complete=True,extended_regression_passed=ok,
    note='Per-report quality/performance/semantic/deployment conclusions remain separate; completion does not mean all requirements passed.')
 path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':
 try:main()
 except Exception as exc:print('Final evaluation stopped; error type='+type(exc).__name__);raise SystemExit(1)
