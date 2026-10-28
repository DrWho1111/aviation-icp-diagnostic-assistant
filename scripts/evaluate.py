from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import time
from datetime import datetime
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"backend"))
from icp.engine import Engine, route
from icp.engineering import reliability
from icp.graphs import graph_for
from icp.knowledge import Knowledge
from icp.storage import Store
from icp.tools import Tools


async def scenario(name, kb, tools):
    engine = Engine(tools, Store("sqlite:///:memory:"))
    async def waiting():
        run = engine.new("画面卡住但后台数据在更新，怎么排查？", "evaluation-session")
        await engine.process(run)
        assert run["status"]=="needs_confirmation"
        assert [t["tool"] for t in run["trace"]]==["Fault_Reasoning_Engine"]
        return run
    def confirm(run, **kw):
        return engine.confirm(run["id"], kw.get("token",run["confirmation_token"]), kw.get("fid","FUNC-410"), kw.get("version",kb.version), "evaluation-session")
    if name in {"confirmation","idempotent_confirmation","stale_confirmation","invalid_candidate","old_version"}:
        run = await waiting()
        if name=="confirmation":
            run, new = confirm(run)
            assert new
            await engine.process(run, confirmed=True)
            assert run["status"]=="completed" and len(run["evidence"])>3
            assert set(run["result"]["coverage"])=={"FTA_Query","FMECA_Query","RBD_Query","Fault_History_Query"}
        elif name=="idempotent_confirmation":
            _, first = confirm(run)
            again, second = confirm(run)
            assert first and not second
        else:
            kwargs={"token":"expired"} if name=="stale_confirmation" else {"fid":"FUNC-999"} if name=="invalid_candidate" else {"version":"old-version"}
            try:
                confirm(run,**kwargs)
            except ValueError:
                return
            raise AssertionError("非法确认被接受")
    elif name=="shared_event":
        graph=graph_for(kb,"fta","FTA-SYN-004")
        event="EVT-SYN-004-BE-04"
        assert sum(n["id"]==event for n in graph["nodes"])==1
        assert sum(e["source"]==event for e in graph["edges"])==2
        result=await tools.execute("FTA_Query",{"query":"FTA-SYN-004"})
        assert f"{result['data'][0]['probability']:.3e}"=="1.063e-03"
    elif name=="invalid_time":
        for value in [-1, float("nan"), float("inf")]:
            try:
                reliability("SERIES(A)",{"A":.001},value)
            except ValueError:
                continue
            raise AssertionError("非法时间被接受")
    elif name=="no_device_execution":
        result=await tools.execute("MFL_PFL_Query",{"query":"GetICUFaultStatus"})
        assert result["status"]=="ok" and all(r["fields"]["device_api_executable"] is False for r in result["data"])
        direct=await tools.execute("Fault_Reasoning_Engine",{"query":"画面卡住","confirmed_function_id":"FUNC-410"})
        assert direct["status"]=="needs_confirmation"
    elif name=="no_fictitious_requirement":
        assert not any(e["entity_type"]=="SystemRequirement" for e in kb.data["entity_chunks"])
        assert not any("燃油" in n["label"] for g in kb.data["graphs"] for n in g["nodes"])
    elif name=="source_integrity":
        for record in kb.records.values():
            assert record["evidence_ids"]
            for eid in record["evidence_ids"]:
                evidence=kb.data["evidence"][eid]
                document=next(d for d in kb.data["documents"] if d["id"]==evidence["document_id"])
                path=ROOT/document["path"]
                assert path.is_file()
        for doc in kb.data["documents"]:
            assert hashlib.sha256((ROOT/doc["path"]).read_bytes()).hexdigest()==doc["sha256"]
    elif name=="restart_recovery":
        with tempfile.TemporaryDirectory() as temp:
            url="sqlite:///"+(Path(temp)/"sessions.db").as_posix()
            first=Engine(tools,Store(url))
            run=first.new("画面卡住，怎么排查？","evaluation-session")
            await first.process(run)
            second=Store(url)
            second.recover_interrupted()
            assert second.get(run["id"])["status"]=="needs_confirmation"
            first.store.engine.dispose();second.engine.dispose()
    elif name=="bounded_retry":
        import icp.integrations as integrations
        import httpx
        attempts=[]
        original=integrations.httpx.AsyncClient
        class FailingClient:
            def __init__(self, **kw): pass
            async def __aenter__(self): return self
            async def __aexit__(self,*args): pass
            async def request(self,*args,**kwargs):
                attempts.append(True)
                raise httpx.ConnectTimeout("sensitive-provider-detail")
        integrations.httpx.AsyncClient=FailingClient
        try:
            try:
                await integrations.request_json("POST","http://localhost/test",key="test-only-placeholder")
            except integrations.IntegrationError as exc:
                assert len(attempts)==2 and "sensitive-provider-detail" not in str(exc) and "placeholder" not in str(exc)
            else:
                raise AssertionError("连接失败未报告")
        finally:
            integrations.httpx.AsyncClient=original
    else:
        raise AssertionError("未知评测场景")


async def evaluate(case, kb, tools):
    if case.get("route"):
        assert route(case["question"])==case["route"], f"意图应为{case['route']}，实际为{route(case['question'])}"
    if case.get("scenario"):
        await scenario(case["scenario"],kb,tools)
    if case.get("tool"):
        params={"query":case["question"],**case.get("params",{})}
        result=await tools.execute(case["tool"],params)
        assert result["status"]==case["status"], f"状态应为{case['status']}，实际为{result['status']}"
        if case.get("record_id"):
            assert case["record_id"] in [r["id"] for r in result["data"]], "未召回期望记录"
        if case.get("model_id"):
            assert any(r.get("model",r)["id"]==case["model_id"] for r in result["data"]), "模型不符"
        if case.get("probability"):
            assert f"{result['data'][0]['probability']:.3e}"==case["probability"]
        if case.get("reliability"):
            assert f"{result['data'][0]['reliability']:.8f}"==case["reliability"]


async def main():
    kb=Knowledge();tools=Tools(kb)
    cases_path=ROOT/"eval/core_cases.json"
    cases=json.loads(cases_path.read_text(encoding="utf-8"))
    results=[]
    # Core evaluation measures local deterministic tools, not LLM or platform success.
    saved={k:os.environ.pop(k,None) for k in ["RAGFLOW_API_KEY","LLM_MODEL","TOOL_BACKEND","DIFY_AGENT_API_KEY","DIFY_NATIVE_MAIN_KEYS_FILE","DIFY_NATIVE_DIAGNOSIS_KEYS_FILE"]}
    try:
        for case in cases:
            start=time.perf_counter()
            try:
                await evaluate(case,kb,tools)
                passed,error=True,None
            except Exception as exc:
                passed,error=False,str(exc) if isinstance(exc,AssertionError) else type(exc).__name__
            results.append({**case,"passed":passed,"error":error,"elapsed_ms":round((time.perf_counter()-start)*1000,2)})
    finally:
        for key,value in saved.items():
            if value is not None: os.environ[key]=value
    output={"status":"completed","mode":"local","corpus_version":kb.version,
            "run_id":datetime.now(ZoneInfo("Asia/Shanghai")).strftime("core-%Y%m%d-%H%M%S"),"timezone":"Asia/Shanghai","case_file_sha256":hashlib.sha256(cases_path.read_bytes()).hexdigest(),
            "summary":{"total":len(results),"passed":sum(c["passed"] for c in results),"failed":sum(not c["passed"] for c in results)},
            "limitations":["未测量模型意图路由准确率或向量Recall@6","未证明Dify、RAGFlow、Neo4j和PostgreSQL已部署","development与acceptance分别记录；数据与计算全量基准另外验证"],
            "cases":results}
    (ROOT/"outputs").mkdir(exist_ok=True)
    (ROOT/"outputs/evaluation.json").write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(output["summary"],ensure_ascii=False))
    for case in results:
        if not case["passed"]: print(case["id"],case["error"])
    return output


if __name__=="__main__":
    result=asyncio.run(main())
    raise SystemExit(bool(result["summary"]["failed"]))
