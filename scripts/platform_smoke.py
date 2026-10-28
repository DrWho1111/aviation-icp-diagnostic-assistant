"""Verify live storage and published Dify tools without exposing credentials."""
from __future__ import annotations

import asyncio
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
import httpx

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"backend"))
load_dotenv(ROOT/".env")
from icp.integrations import dify_tool
from icp.knowledge import Knowledge
from icp.graphs import graph_for
from icp.graph_store import read_graph


async def main(base_url='http://127.0.0.1:8000'):
    report={"date":datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),"mode":"live_platforms","checks":[]}
    def record(name,passed,**details):
        report["checks"].append({"name":name,"passed":bool(passed),**details})
        print(json.dumps({"name":name,"passed":bool(passed)},ensure_ascii=False),flush=True)
        (ROOT/"outputs/platform-smoke.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    kb=Knowledge(use_database=True)
    record("postgres_knowledge",bool(kb.records),records=len(kb.records),version=kb.version)
    graph=read_graph(graph_for(kb,"fta","FTA-SYN-004"))
    record("neo4j_graph",graph.get("storage")=="neo4j",nodes=len(graph["nodes"]),edges=len(graph["edges"]))
    with httpx.Client(trust_env=False) as client:
        health=client.get(base_url.rstrip('/')+"/api/health").raise_for_status().json()
        record("backend_postgres_sessions",health["components"]["storage"]=="postgresql")
        graph=client.get(base_url.rstrip('/')+"/api/graphs/fta/FTA-SYN-004").raise_for_status().json()
        record("backend_neo4j_graph",graph.get("storage")=="neo4j")
    os.environ["DIFY_TOOL_KEYS_FILE"]=str(ROOT/".secrets/dify-tools.json")
    cases=[c for c in json.loads((ROOT/"eval/core_cases.json").read_text(encoding="utf-8")) if c.get("tool")]
    cases += [{"id":"PLATFORM-diagnosis-entry","tool":"Fault_Reasoning_Engine","question":"画面卡住但后台数据在更新，怎么排查？","status":"needs_confirmation"},
              {"id":"PLATFORM-end","tool":"End","question":"结束","status":"ok"}]
    for case in cases:
        try:
            result=await dify_tool(case["tool"],{"query":case["question"],**case.get("params",{})},"platform-validation")
            checks={"status":result["status"]==case["status"],"version":result["corpus_version"]==kb.version}
            if case.get("record_id"): checks["record_id"]=case["record_id"] in [r["id"] for r in result["data"]]
            if case.get("model_id"): checks["model_id"]=any(r.get("model",r)["id"]==case["model_id"] for r in result["data"])
            if case.get("probability"): checks["probability"]=f"{result['data'][0]['probability']:.3e}"==case["probability"]
            if case.get("reliability"): checks["reliability"]=f"{result['data'][0]['reliability']:.8f}"==case["reliability"]
            record(case["id"],all(checks.values()),tool=case["tool"],checks=checks,result=result)
        except Exception as exc:
            record(case["id"],False,tool=case["tool"],error_type=type(exc).__name__)
    report["passed"]=all(c["passed"] for c in report["checks"])
    report["limitations"]=["工作流和存储验证；模型规划、RAGFlow索引与重排分别验收"]
    (ROOT/"outputs/platform-smoke.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    return report["passed"]


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument('--base-url',default='http://127.0.0.1:8000')
    try: raise SystemExit(not asyncio.run(main(parser.parse_args().base_url)))
    except Exception as exc:
        print("Live platform verification stopped; error type="+type(exc).__name__)
        raise SystemExit(1)
