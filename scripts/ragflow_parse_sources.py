"""Queue original-source parsing and the four project Graph compilation jobs."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from dotenv import load_dotenv
import httpx

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"backend"))
from icp.knowledge import Knowledge


def main(graphs=False):
    load_dotenv(ROOT/".env")
    kb=Knowledge()
    imported=json.loads((ROOT/".secrets/ragflow-import.json").read_text(encoding="utf-8"))
    if imported.get('version')!=kb.version:
        imported=json.loads((ROOT/('.secrets/ragflow-import-'+kb.version+'.json')).read_text(encoding='utf-8'))
    if imported["version"]!=kb.version: raise RuntimeError("RAGFlow import checkpoint version mismatch")
    checkpoint=ROOT/".secrets/ragflow-parsing.json"
    state=json.loads(checkpoint.read_text(encoding="utf-8")) if checkpoint.exists() else {"version":kb.version,"originals":{},"graphs":{}}
    if state.get('version')!=kb.version:
        checkpoint=checkpoint.with_name('ragflow-parsing-'+kb.version+'.json')
        state=json.loads(checkpoint.read_text(encoding='utf-8')) if checkpoint.exists() else {'version':kb.version,'originals':{},'graphs':{}}
    if state["version"]!=kb.version: raise RuntimeError("RAGFlow parsing checkpoint version mismatch")
    def save(): checkpoint.write_text(json.dumps(state,indent=2),encoding="utf-8")
    with httpx.Client(base_url=os.getenv("RAGFLOW_BASE_URL","http://127.0.0.1:9380")+"/api/v1",headers={"Authorization":"Bearer "+os.environ["RAGFLOW_API_KEY"]},trust_env=False,timeout=120) as client:
        def request(method,path,**kwargs):
            response=client.request(method,path,**kwargs)
            response.raise_for_status();value=response.json()
            if value.get("code")!=0:
                (ROOT/".secrets/ragflow-last-parse-error.json").write_text(json.dumps({"path":path,"body":value}),encoding="utf-8")
                raise RuntimeError("RAGFlow parsing request failed; details retained privately")
            return value.get("data")
        def status(ds,did):
            docs=request("GET",f"/datasets/{ds}/documents",params={"page_size":100})
            return next(d for d in docs["docs"] if d["id"]==did)
        for source in kb.data["documents"]:
            sid=source["id"]
            if sid not in imported.get("original_documents",{}): continue
            ds=imported["datasets"]["original-"+source["corpus"]];did=imported["original_documents"][sid]
            current=status(ds,did)
            if str(current.get("run")) in {"3","DONE","1","RUNNING","5","SCHEDULE"}: continue
            request("PATCH",f"/datasets/{ds}/documents/{did}",json={"parser_config":{"layout_recognize":"Plain Text","auto_keywords":0,"auto_questions":0,"chunk_token_num":512}})
            request("PATCH",f"/datasets/{ds}/documents/metadatas",json={"selector":{"document_ids":[did]},"updates":[{"key":"source_id","value":sid},{"key":"corpus","value":source["corpus"]},{"key":"corpus_version","value":kb.version},{"key":"document_name","value":source["name"]}]})
            request("POST",f"/datasets/{ds}/chunks",json={"document_ids":[did]})
            state["originals"][sid]={"dataset_id":ds,"document_id":did,"status":"queued"};save()
            print(json.dumps({"source_id":sid,"status":"parsing_queued"}),flush=True)
        if graphs:
            templates=json.loads((ROOT/"deploy/ragflow-graph-templates.json").read_text(encoding="utf-8"))["groups"]
            groups=json.loads((ROOT/".secrets/ragflow-graph-templates.json").read_text(encoding="utf-8"))
            for group in templates:
                name=group["name"]
                item=state["graphs"].setdefault(name,{"documents":{}})
                config={"layout_recognize":"Plain Text","auto_keywords":0,"auto_questions":0,"chunk_token_num":512,"compilation_template_group_id":[groups[name]]}
                if "dataset_id" not in item:
                    dataset=request("POST","/datasets",json={"name":kb.version+"-"+name,"language":"Chinese","embedding_model":os.environ["RAGFLOW_EMBEDDING_MODEL"],"chunk_method":"naive","parser_config":config})
                    item["dataset_id"]=dataset["id"];save()
                ds=item["dataset_id"]
                for sid in group["source_documents"]:
                    source=next(d for d in kb.data["documents"] if d["id"]==sid)
                    if sid not in item["documents"]:
                        with (ROOT/source["path"]).open("rb") as handle:
                            value=request("POST",f"/datasets/{ds}/documents",files={"file":(source["name"],handle)})
                        item["documents"][sid]=(value[0] if isinstance(value,list) else value)["id"];save()
                    did=item["documents"][sid];current=status(ds,did)
                    if str(current.get("run")) not in {"3","DONE","1","RUNNING","5","SCHEDULE"}:
                        request("POST",f"/datasets/{ds}/chunks",json={"document_ids":[did]})
                    print(json.dumps({"graph":name,"source_id":sid,"status":"queued_or_existing"}),flush=True)
        save()
    print("Parsing jobs queued; completion, index coverage and Graph output must be checked separately.")


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--graphs",action="store_true")
    try: main(parser.parse_args().graphs)
    except Exception as exc:
        print("RAGFlow source parsing stopped; error type="+type(exc).__name__)
        raise SystemExit(1)
