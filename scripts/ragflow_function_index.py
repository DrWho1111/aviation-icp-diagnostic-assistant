"""Split this project's function-name chunks into a dedicated retrieval scope."""
import json
import os
from pathlib import Path
import re
import sys

from dotenv import load_dotenv
import httpx

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"backend"))
from icp.knowledge import Knowledge


def main():
    load_dotenv(ROOT/".env");kb=Knowledge()
    path=ROOT/".secrets/ragflow-import.json";state=json.loads(path.read_text(encoding="utf-8"))
    if state["version"]!=kb.version: raise RuntimeError("Knowledge version mismatch")
    ds=state["datasets"]["entity"];old=state["documents"]["entity"]
    expected={e["entity_id"]:e for e in kb.data["entity_chunks"] if e["entity_type"] in {"TopLevelFunction","SubFunction"}}
    def save(): path.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding="utf-8")
    with httpx.Client(base_url=os.environ["RAGFLOW_BASE_URL"]+"/api/v1",headers={"Authorization":"Bearer "+os.environ["RAGFLOW_API_KEY"]},trust_env=False,timeout=120) as client:
        def request(method,p,**kwargs):
            r=client.request(method,p,**kwargs);r.raise_for_status();v=r.json()
            if v.get("code")!=0: raise RuntimeError("Function-name index request failed")
            return v.get("data")
        def chunks(doc):
            result=[]
            for page in range(1,10):
                batch=request("GET",f"/datasets/{ds}/documents/{doc}/chunks",params={"page":page,"page_size":100})["chunks"]
                result.extend(batch)
                if len(batch)<100:break
            return result
        if "function_document_id" not in state:
            v=request("POST",f"/datasets/{ds}/documents?type=empty",json={"name":"function-entities-curated.txt"})
            state["function_document_id"]=(v[0] if isinstance(v,list) else v)["id"];save()
        new=state["function_document_id"]
        indexed={rid for c in chunks(new) for rid in re.findall(r"\[RECORD_ID=([^\]]+)\]",c["content"])}
        remove=[]
        for chunk in chunks(old):
            match=re.search(r"\[RECORD_ID=(FUNC-\d+)\]",chunk["content"])
            if not match or match[1] not in expected:continue
            if f"[CORPUS_VERSION={kb.version}]" not in chunk["content"]:raise RuntimeError("Foreign chunk rejected")
            eid=match[1];entity=expected[eid]
            if eid not in indexed:
                request("POST",f"/datasets/{ds}/documents/{new}/chunks",json={"content":chunk["content"],"important_keywords":[eid,entity["type_label"]],"tag_kwd":["entity","synthetic",kb.version,entity["entity_type"],*entity["evidence_ids"]]})
                indexed.add(eid)
            remove.append(chunk["id"])
        actual={rid for c in chunks(new) for rid in re.findall(r"\[RECORD_ID=([^\]]+)\]",c["content"])}
        if actual!=set(expected):raise RuntimeError("Function-name index is incomplete; original chunks retained")
        if remove:request("DELETE",f"/datasets/{ds}/documents/{old}/chunks",json={"chunk_ids":remove})
        state["function_scope_verified"]=True;save()
        print("Thirty source-labelled function names indexed separately; replaced copies removed only from the original project document.")


if __name__=="__main__":
    try:main()
    except Exception as exc:
        print("Function-name indexing stopped; error type="+type(exc).__name__)
        raise SystemExit(1)
