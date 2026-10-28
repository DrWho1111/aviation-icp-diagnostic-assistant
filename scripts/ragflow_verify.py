"""Verify live RAGFlow indexing, name retrieval, citations and native Graph output."""
from __future__ import annotations

import asyncio
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
import os
from pathlib import Path
import re
import sys
import time
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
import httpx

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"backend"))
load_dotenv(ROOT/".env")
from icp.knowledge import Knowledge
from icp.tools import Tools


def main(enable=False):
    kb=Knowledge(use_database=True)
    state=json.loads((ROOT/".secrets/ragflow-import.json").read_text(encoding="utf-8"))
    if state["version"]!=kb.version: raise RuntimeError("Knowledge version mismatch")
    datasets=state["datasets"]
    synthetic=[did for name,did in datasets.items() if name not in {"research","entity"} and not name.startswith("original-")]
    os.environ.update({"RAGFLOW_SYNTHETIC_DATASET_IDS":",".join(synthetic),"RAGFLOW_RESEARCH_DATASET_IDS":datasets["research"],"RAGFLOW_ENTITY_DATASET_IDS":datasets["entity"],"RAGFLOW_FUNCTION_DOCUMENT_IDS":state.get("function_document_id","")})
    report={"date":datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),"version":kb.version,"checks":[],"limitations":["固定开发场景与索引完整性检查，不是独立检索准确率评测；原始资料解析和Graph候选完整性单独记录"]}
    def record(name,passed,**details):
        report["checks"].append({"name":name,"passed":bool(passed),**details})
        (ROOT/"outputs/ragflow-verification.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
        print(json.dumps({"name":name,"passed":bool(passed)},ensure_ascii=False),flush=True)
    with httpx.Client(base_url=os.environ["RAGFLOW_BASE_URL"].rstrip("/")+"/api/v1",headers={"Authorization":"Bearer "+os.environ["RAGFLOW_API_KEY"]},trust_env=False,timeout=60) as client:
        def request(path,**kwargs):
            response=client.get(path,**kwargs);response.raise_for_status();value=response.json()
            if value.get("code")!=0: raise RuntimeError("RAGFlow verification API failed")
            return value.get("data")
        for group,completed in state["chunks"].items():
            ds=datasets[group];did=state["documents"][group]
            docs=request(f"/datasets/{ds}/documents",params={"page_size":100})["docs"]
            doc=next(d for d in docs if d["id"]==did)
            actual=doc.get("chunk_count",doc.get("chunk_num",0))
            if group=="entity" and state.get("function_document_id"):
                other=next(d for d in docs if d["id"]==state["function_document_id"])
                actual+=other.get("chunk_count",other.get("chunk_num",0))
            record("index-"+group,actual==len(completed),expected=len(completed),actual=actual)
        expected_total=len(kb.data["chunks"])+len(kb.data["entity_chunks"])
        total=sum(len(c) for c in state["chunks"].values())
        record("all-curated-chunks",total==expected_total,expected=expected_total,actual=total)
        ds=datasets["entity"]
        chunks=[]
        documents=[state["documents"]["entity"]]
        if state.get("function_document_id"):documents.append(state["function_document_id"])
        for did in documents:
            for page in range(1,10):
                data=request(f"/datasets/{ds}/documents/{did}/chunks",params={"page":page,"page_size":100})
                batch=data.get("chunks",[]);chunks.extend(batch)
                if len(batch)<100: break
        # List-search projections omit tag_kwd in this pinned RAGFlow version;
        # direct chunk reads expose the stored metadata without that projection.
        def metadata(chunk):
            return request(f"/datasets/{ds}/documents/{chunk['document_id']}/chunks",params={"id":chunk["id"]})["chunks"][0]
        with ThreadPoolExecutor(max_workers=6) as pool:
            stored=list(pool.map(metadata,chunks))
        expected=Counter(e["entity_type"] for e in kb.data["entity_chunks"])
        actual=Counter(tag for chunk in stored for tag in chunk.get("tag_kwd",[]) if tag in expected)
        ids=[match for chunk in chunks for match in re.findall(r"\[RECORD_ID=([^\]]+)\]",chunk.get("content",""))]
        record("entity-types-and-unique-names",actual==expected and len(ids)==len(set(ids))==len(kb.data["entity_chunks"]),expected_types=dict(expected),actual_types=dict(actual),unique_ids=len(set(ids)))
        parsing=json.loads((ROOT/".secrets/ragflow-parsing.json").read_text(encoding="utf-8"))
        output=ROOT/"outputs/ragflow-graphs";output.mkdir(exist_ok=True)
        for name,item in parsing.get("graphs",{}).items():
            for source,did in item["documents"].items():
                ds=item["dataset_id"]
                docs=request(f"/datasets/{ds}/documents",params={"page_size":100})["docs"]
                doc=next(d for d in docs if d["id"]==did)
                graph=request(f"/datasets/{ds}/documents/{did}/structure/graph")
                (output/(name+".json")).write_text(json.dumps(graph,ensure_ascii=False,indent=2),encoding="utf-8")
                types=Counter(entity.get("type") for t in graph.get("templates",[]) for entity in t.get("entities",[]))
                record("native-graph-"+name,str(doc["run"]) in {"DONE","3"} and graph.get("total_entities",0)>0 and graph.get("total_relations",0)>0,source=source,status=doc["run"],entities=graph.get("total_entities"),relations=graph.get("total_relations"),entity_types=dict(types),note="模型候选，不替代已经核对的工程门逻辑与计算基准")
        original_status=[]
        for source,did in state.get("original_documents",{}).items():
            document=next(d for d in kb.data["documents"] if d["id"]==source)
            ds=datasets["original-"+document["corpus"]]
            doc=next(d for d in request(f"/datasets/{ds}/documents",params={"page_size":100})["docs"] if d["id"]==did)
            original_status.append({"source_id":source,"status":doc["run"],"chunks":doc.get("chunk_count",0)})
        report["original_documents"]=original_status
        record("original-source-parsing",bool(original_status) and all(str(d["status"]) in {"DONE","3"} and d["chunks"]>0 for d in original_status),documents=len(original_status),completed=sum(str(d["status"]) in {"DONE","3"} for d in original_status))
    tools=Tools(kb)
    for question,expected in [("画面卡住但后台数据在更新，怎么排查？","FUNC-410"),("电源闪断一下系统就重启对应哪个功能","FUNC-140"),("网络流量一大就丢一片包对应哪个功能","FUNC-320")]:
        started=time.monotonic();result=asyncio.run(tools.execute("Function_Locator",{"query":question}))
        candidates=[r["id"] for r in result["data"]]
        record("function-top3-"+expected,result["status"]=="ok" and expected in candidates and len(candidates)==3 and bool(result["evidence"]),question=question,candidates=candidates,seconds=round(time.monotonic()-started,2),result=result)
    # The source collection does not define “功能架构”. Refusing to stitch
    # unrelated material into that definition is the required boundary,
    # rather than a positive retrieval success. Keep a source-backed positive
    # research query as well; do not count any refusal as successful retrieval.
    for corpus,question in [("research","综合核心处理机的模块化设计和资源共享有哪些特点？"),("synthetic","电源调理与分配的功能是什么？")]:
        started=time.monotonic();result=asyncio.run(tools.execute("Hybrid_Search",{"query":question,"corpus":corpus}))
        record("hybrid-rerank-citations-"+corpus,result["status"]=="ok" and bool(result["evidence"]) and all(e["corpus"]==corpus for e in result["evidence"]),seconds=round(time.monotonic()-started,2),result=result)
    from icp.retrieval_boundaries import term_in_sources
    question='系统工程中的功能架构是什么？'
    result=asyncio.run(tools.execute('Hybrid_Search',{'query':question,'corpus':'research'}))
    supported=term_in_sources('功能架构',list(kb.data['evidence'].values()))
    record('unsupported-concept-is-not-fabricated',not supported and result['status'] in {'not_covered','not_found'},
        question=question,source_collection_has_requested_term=supported,result=result,
        note='Retains the previously failed positive check as a source-absence boundary; separate supported queries must pass above.')
    report["passed"]=all(c["passed"] for c in report["checks"])
    if enable and report["passed"]:
        sys.path.insert(0,str(ROOT))
        from scripts.configure_platforms import replace_env
        changes={"RAGFLOW_DATASET_IDS":",".join(synthetic),"RAGFLOW_SYNTHETIC_DATASET_IDS":",".join(synthetic),"RAGFLOW_RESEARCH_DATASET_IDS":datasets["research"],"RAGFLOW_ENTITY_DATASET_IDS":datasets["entity"],"RAGFLOW_FUNCTION_DOCUMENT_IDS":state.get("function_document_id","")}
        path=ROOT/".env"
        path.write_text(replace_env(path.read_text(encoding="utf-8"),changes),encoding="utf-8")
        report["enabled"]=True
        print("Verified RAGFlow datasets enabled in private configuration; restart application to apply.",flush=True)
    (ROOT/"outputs/ragflow-verification.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    return report["passed"]


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--enable",action="store_true")
    try: raise SystemExit(not main(parser.parse_args().enable))
    except Exception as exc:
        print("RAGFlow verification stopped; error type="+type(exc).__name__)
        raise SystemExit(1)
