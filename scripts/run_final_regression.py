"""Wait for owned graph work, then run real validation checks serially."""
import json
from pathlib import Path
import subprocess
import sys
import time
import httpx

ROOT=Path(__file__).resolve().parents[1]
DOCKER=r'D:\Apps\Docker\resources\bin\docker.exe'


def main():
 deadline=time.monotonic()+3600
 while True:
  state=json.loads((ROOT/'outputs/scoped-graph-compilation.json').read_text(encoding='utf-8'))
  if state.get('complete'):break
  if time.monotonic()>deadline:raise RuntimeError('Graph compilation did not finish; no model tests started')
  time.sleep(5)
 profile=json.loads((ROOT/'outputs/graph-model-profile.json').read_text(encoding='utf-8'))
 with httpx.Client(trust_env=False,timeout=90) as client:
  client.post('http://127.0.0.1:11434/api/generate',json={'model':profile['name'],'keep_alive':0,'stream':False}).raise_for_status()
 commands=[
  [DOCKER,'build','-f','deploy/validation.Dockerfile','-t','icp-final-validation:20261024','.'],
  [DOCKER,'compose','-f','deploy/validation.compose.yaml','up','-d','--force-recreate'],
  [sys.executable,'scripts/run_extended_eval.py','--base-url','http://127.0.0.1:8002','--split','acceptance','--only-ids','routing-13,routing-16,routing-17,routing-18,boundary-08,boundary-12,boundary-16'],
  [sys.executable,'scripts/native_end_to_end.py'],
  [sys.executable,'scripts/model_integration.py','--base-url','http://127.0.0.1:8002','--planner','dify','--fullstack','--output','outputs/fullstack-native-loop-verified.json'],
  [sys.executable,'scripts/dialogue_boundary_check.py'],
 ]
 report={'date':'2026-10-24','scope':'serial actual staging regression after graph worker completion','steps':[],'complete':False}
 for index,command in enumerate(commands):
  started=time.monotonic();result=subprocess.run(command,cwd=ROOT)
  if len(command)>1 and command[1]=='scripts/native_end_to_end.py':
   native=json.loads((ROOT/'outputs/native-loop-end-to-end.json').read_text(encoding='utf-8'))
   if not native.get('passed'):result.returncode=1
  report['steps'].append({'step':index+1,'script':command[1:],'exit_code':result.returncode,'seconds':round(time.monotonic()-started,2)})
  (ROOT/'outputs/final-serial-regression.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
  if result.returncode:print('Regression stopped at failed step; evidence retained.',flush=True);return False
 report.update(complete=True,passed=True)
 (ROOT/'outputs/final-serial-regression.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
 return True


if __name__=='__main__':raise SystemExit(not main())
