"""Validate an actual local Qwen instance before enabling it in the application."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import time
from zoneinfo import ZoneInfo

import httpx

ROOT=Path(__file__).resolve().parents[1]


def main(enable=False):
    results=[]
    model="qwen3:8b"
    with httpx.Client(base_url="http://127.0.0.1:11434",timeout=180,trust_env=False) as client:
        try:
            tags=client.get("/api/tags").raise_for_status().json()
        except (httpx.HTTPError, ValueError):
            print("Local Ollama is unavailable. Complete docs/本地模型安装.md first.")
            return False
        info=next((m for m in tags.get("models",[]) if m.get("name")==model),None)
        if not info:
            print("qwen3:8b is not downloaded. Run scripts/start-ollama.ps1 -PullModel.")
            return False
        tests=[
            ("chinese",{"messages":[{"role":"user","content":"请用一句简短中文说明：API可以调用本地模型吗？"}]}),
            ("json",{"messages":[{"role":"user","content":"只返回JSON对象，字段ok为true，language为zh。"}],
                     "format":{"type":"object","properties":{"ok":{"type":"boolean"},"language":{"type":"string"}},"required":["ok","language"]}}),
            ("tool_call",{"messages":[{"role":"user","content":"请调用HMC_Query查询91810001-**-0001，不要直接回答含义。"}],
                          "tools":[{"type":"function","function":{"name":"HMC_Query","description":"查询完整HMC故障代码", "parameters":{"type":"object","properties":{"query":{"type":"string"}},"required":["query"]}}}]})]
        for name,body in tests:
            start=time.perf_counter()
            try:
                value=client.post("/api/chat",json={"model":model,"stream":False,"think":False,
                    "options":{"temperature":0,"num_ctx":8192,"num_predict":384},**body}).raise_for_status().json()
                message=value.get("message",{})
                text=message.get("content","")
                if name=="chinese":
                    passed=bool(re.search(r"[\u4e00-\u9fff]",text)) and bool(text.strip())
                elif name=="json":
                    structured=json.loads(text)
                    passed=structured.get("ok") is True and structured.get("language")=="zh"
                else:
                    calls=message.get("tool_calls",[])
                    passed=any(c.get("function",{}).get("name")=="HMC_Query" and
                               "91810001-**-0001" in json.dumps(c.get("function",{}).get("arguments",{})) for c in calls)
                results.append({"test":name,"passed":passed,"elapsed_seconds":round(time.perf_counter()-start,2),
                                "response":message,"tokens_per_second":round(value.get("eval_count",0)/(value.get("eval_duration",1)/1e9),2) if value.get("eval_duration") else None})
            except (httpx.HTTPError,ValueError,TypeError,KeyError):
                results.append({"test":name,"passed":False,"error":"本机模型调用或格式验证失败"})
        residency=client.get("/api/ps").raise_for_status().json()
    passed=all(r["passed"] for r in results)
    output={"date":datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),"model":model,"model_digest":info.get("digest"),"model_bytes":info.get("size"),"passed":passed,"tests":results,"residency":residency}
    (ROOT/"outputs").mkdir(exist_ok=True)
    (ROOT/"outputs/model-smoke.json").write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding="utf-8")
    if passed and enable:
        env=ROOT/".env"
        value=env.read_text(encoding="utf-8")
        value=re.sub(r"(?m)^LLM_MODEL=.*$","LLM_MODEL="+model,value)
        if re.search(r"(?m)^LLM_API_STYLE=",value):
            value=re.sub(r"(?m)^LLM_API_STYLE=.*$","LLM_API_STYLE=ollama",value)
        else:
            value+="\nLLM_API_STYLE=ollama\n"
        env.write_text(value,encoding="utf-8")
        print("Local model verified and enabled. Restart the backend to load the configuration.")
    print(json.dumps({"passed":passed,"tests":[{"name":r["test"],"passed":r["passed"]} for r in results]},ensure_ascii=False))
    return passed


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--enable",action="store_true")
    args=parser.parse_args()
    raise SystemExit(not main(args.enable))
