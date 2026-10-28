"""Probe actual planner generation after container startup; HTTP readiness is insufficient."""
import asyncio
from datetime import datetime
import json
import os
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from icp import planner
from icp.integrations import IntegrationError


async def main():
    report={'date':datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),'passed':False,
            'scope':'实际规划响应可用性，不是准确率或性能测量','attempts':0}
    for attempt in range(2):
        report['attempts']=attempt+1
        try:
            value=await planner.choose('列出系统全部子系统',[],'startup-readiness-check')
            report['passed']=value['tool']=='Hybrid_Search' and value['corpus']=='synthetic' and not value['missing_fields']
            report['planner']='dify' if os.getenv('DIFY_AGENT_API_KEY') else 'local_llm'
        except IntegrationError:
            report['passed']=False
        if report['passed']:break
        if attempt==0:await asyncio.sleep(1)
    (ROOT/'outputs/startup-model-probe.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Actual planner startup probe: '+('passed' if report['passed'] else 'not ready; inspect sanitized platform logs'),flush=True)
    return report['passed']


if __name__=='__main__':raise SystemExit(not asyncio.run(main()))
