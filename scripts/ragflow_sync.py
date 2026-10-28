"""Publish source-labelled curated records using the pinned RAGFlow REST API.

Creates only project/version datasets; never deletes the user's datasets.
Checkpoint prevents duplicate chunks when a completed import is rerun.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

import httpx
from dotenv import load_dotenv

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"backend"))
from icp.knowledge import Knowledge


def main():
    load_dotenv(ROOT/".env")
    key=os.getenv("RAGFLOW_API_KEY","")
    embedding=os.getenv("RAGFLOW_EMBEDDING_MODEL","")
    if not key or not embedding:
        raise ValueError("先在后端.env配置RAGFlow凭据和已注册的嵌入模型名称")
    kb=Knowledge()
    checkpoint=ROOT/".secrets/ragflow-import.json"
    checkpoint.parent.mkdir(exist_ok=True)
    state=json.loads(checkpoint.read_text(encoding="utf-8")) if checkpoint.exists() else {}
    if state and state.get("version")!=kb.version:
        checkpoint=checkpoint.with_name("ragflow-import-"+kb.version+".json")
        state=json.loads(checkpoint.read_text(encoding="utf-8")) if checkpoint.exists() else {}
    state.setdefault("version",kb.version);state.setdefault("datasets",{});state.setdefault("documents",{});state.setdefault("chunks",{})
    def save(): checkpoint.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding="utf-8")
    with httpx.Client(base_url=os.getenv("RAGFLOW_BASE_URL","http://127.0.0.1:9380"),headers={"Authorization":"Bearer "+key},timeout=120,trust_env=False) as client:
        def request(method,path,**kwargs):
            response=client.request(method,"/api/v1"+path,**kwargs)
            response.raise_for_status()
            value=response.json()
            if value.get("code")!=0:
                (ROOT/'.secrets/ragflow-import-last-error.json').write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8')
                raise RuntimeError("RAGFlow返回非成功业务码；私有诊断已保存")
            return value.get("data")
        def dataset(group):
            if group not in state["datasets"]:
                value=request("POST","/datasets",json={"name":"icp-"+kb.version+"-"+group,"language":"Chinese","embedding_model":embedding,"chunk_method":"naive"})
                state["datasets"][group]=value["id"];save()
            return state["datasets"][group]
        def document(group,chunk):
            ds=dataset(group)
            if group=="entity" and chunk.get("entity_type") in {"TopLevelFunction","SubFunction"}:
                if "function_document_id" not in state:
                    value=request("POST",f"/datasets/{ds}/documents?type=empty",json={"name":"function-entities-curated.txt"})
                    state["function_document_id"]=(value[0] if isinstance(value,list) else value)["id"];save()
                return ds,state["function_document_id"]
            if group not in state["documents"]:
                value=request("POST",f"/datasets/{ds}/documents?type=empty",json={"name":group+"-curated.txt"})
                state["documents"][group]=(value[0] if isinstance(value,list) else value)["id"];save()
            return ds,state["documents"][group]
        chunks=list(kb.data["chunks"])
        chunks += [{**e,"kind":"entity","corpus":"synthetic","record_id":e["entity_id"]} for e in kb.data["entity_chunks"]]
        for chunk in chunks:
            group=chunk["kind"]
            cid=chunk["id"]
            completed=state["chunks"].setdefault(group,[])
            if cid in completed: continue
            ds,doc=document(group,chunk)
            source=" ".join("[SOURCE_ID="+eid+"]" for eid in chunk["evidence_ids"])
            locations=[{key:kb.data["evidence"][eid].get(key) for key in ["id","document_id","document","section","page","table","row"]} for eid in chunk["evidence_ids"]]
            content=f"[CORPUS_VERSION={kb.version}] [RECORD_ID={chunk['record_id']}] {source}\n"+chunk["text"]+"\n[SOURCE_METADATA="+json.dumps(locations,ensure_ascii=False)+"]"
            record=kb.records.get(chunk['record_id'])
            if record and record['kind']=='function':
                from icp.retrieval_profile import function_index_text
                content,_=function_index_text(kb,record)
            request("POST",f"/datasets/{ds}/documents/{doc}/chunks",json={"content":content,"important_keywords":[chunk["record_id"],chunk.get("type_label",group)],"tag_kwd":[group,chunk["corpus"],kb.version,chunk.get("entity_type",group),*chunk["evidence_ids"]]})
            completed.append(cid);save()
            if sum(len(items) for items in state["chunks"].values())%50==0:
                print(json.dumps({"indexed_chunks":sum(len(items) for items in state["chunks"].values()),"total_chunks":len(chunks)}),flush=True)
        # Keep original files separately for RAGFlow parsing/Graph compilation inspection.
        state.setdefault("original_documents",{})
        for source in kb.data["documents"]:
            # Requirement documents need full-text/vector management too. Keep
            # them in original-requirements, outside all device-query datasets.
            if source["status"]=="link_only": continue
            sid=source["id"]
            if sid in state["original_documents"]: continue
            ds=dataset("original-"+source["corpus"])
            with (ROOT/source["path"]).open("rb") as handle:
                value=request("POST",f"/datasets/{ds}/documents",files={"file":(source["name"],handle)})
            state["original_documents"][sid]=(value[0] if isinstance(value,list) else value)["id"];save()
    print("RAGFlow import completed. Dataset IDs and checkpoints saved privately; configure datasets from that file.")
    return state


if __name__=="__main__":
    try: main()
    except Exception as exc:
        print("RAGFlow import failed; checkpoint retained. Error type="+type(exc).__name__)
        raise SystemExit(1)
