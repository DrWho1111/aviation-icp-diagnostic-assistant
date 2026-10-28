"""Download/resume BGE-M3 and check real local vectors without publishing a knowledge base."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]
MODEL = "bge-m3:latest"


def cosine(a, b):
    return sum(x*y for x,y in zip(a,b)) / math.sqrt(sum(x*x for x in a)*sum(y*y for y in b))


def main(pull=False):
    out=ROOT/"outputs"
    out.mkdir(exist_ok=True)
    with httpx.Client(base_url="http://127.0.0.1:11434", trust_env=False, timeout=180) as client:
        if pull:
            state={"model":MODEL,"status":"downloading","downloaded":False}
            last=0
            with client.stream("POST","/api/pull",json={"model":MODEL,"stream":True}) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line: continue
                    value=json.loads(line)
                    if value.get("error"): raise RuntimeError("Local model download failed")
                    state.update({k:value[k] for k in ["status","total","completed"] if k in value})
                    if value.get("status")=="success": state["downloaded"]=True
                    if time.monotonic()-last>10 or state["downloaded"]:
                        (out/"embedding-download.json").write_text(json.dumps(state,indent=2),encoding="utf-8")
                        last=time.monotonic()
                    if state["downloaded"]: print("BGE-M3 download completed.",flush=True)
        tags=client.get("/api/tags").raise_for_status().json()
        info=next((m for m in tags.get("models",[]) if m.get("name")==MODEL),None)
        if not info: raise RuntimeError("BGE-M3 has not finished downloading")
        texts=["显示画面冻结，但后台数据继续更新", "屏幕卡住不动，业务数据还在刷新",
               "散热风扇堵转造成温度升高", "主备电源同时失效"]
        start=time.perf_counter()
        result=client.post("/api/embed",json={"model":MODEL,"input":texts,"keep_alive":0}).raise_for_status().json()
        vectors=result.get("embeddings",[])
        shape_ok=(len(vectors)==len(texts) and bool(vectors[0]) and len({len(v) for v in vectors})==1)
        numeric_ok=shape_ok and all(all(math.isfinite(x) for x in v) and sum(x*x for x in v)>0 for v in vectors)
        scores=[cosine(vectors[0],v) for v in vectors[1:]] if numeric_ok else []
        semantic_ok=bool(scores) and scores[0]>max(scores[1:])
        output={"model":MODEL,"digest":info.get("digest"),"model_bytes":info.get("size"),
                "passed":numeric_ok and semantic_ok,"dimensions":len(vectors[0]) if shape_ok else None,
                "vectors":len(vectors),"finite_nonzero":numeric_ok,"similar_description_ranks_first":semantic_ok,
                "cosine_similarities":[round(v,5) for v in scores],"elapsed_seconds":round(time.perf_counter()-start,2),
                "limitations":["仅本地嵌入接口和四条固定文本检查，未建立RAGFlow索引","不代表检索Recall或重排效果"]}
        (out/"embedding-smoke.json").write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding="utf-8")
        print(json.dumps(output,ensure_ascii=False),flush=True)
        return output["passed"]


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--pull",action="store_true")
    try:
        raise SystemExit(not main(parser.parse_args().pull))
    except (httpx.HTTPError, ValueError, TypeError, RuntimeError) as exc:
        print("Embedding preparation failed; download can resume. Error type="+type(exc).__name__)
        raise SystemExit(1)
