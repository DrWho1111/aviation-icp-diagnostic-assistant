"""Check the real local cross-encoder and its RAGFlow-compatible response."""
from datetime import datetime
import json
import math
from pathlib import Path
import time
from zoneinfo import ZoneInfo

import httpx

ROOT=Path(__file__).resolve().parents[1]


def main():
    started=time.monotonic()
    with httpx.Client(base_url="http://127.0.0.1:9390",trust_env=False,timeout=90) as client:
        health=client.get("/health").raise_for_status().json()
        texts=["显示画面合成任务挂起会使前台画面冻结，但后台数据仍可能继续更新。", "电源调理模块负责外部电源调理与稳压。", "天气预报介绍明天的气温和降水。"]
        response=client.post("/rerank",json={"query":"画面卡住但后台数据在更新，应检查什么？","texts":texts,"raw_scores":False,"truncate":False}).raise_for_status().json()
        scores=[item["score"] for item in sorted(response,key=lambda item:item["index"])]
        checks={"cpu_model":health.get("device")=="cpu","finite_scores":len(scores)==3 and all(math.isfinite(s) and 0<=s<=1 for s in scores),"relevant_first":max(range(len(scores)),key=scores.__getitem__)==0,
                "invalid_input_rejected":client.post("/rerank",json={"query":"","texts":[]}).status_code==400}
    report={"date":datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),"model":health.get("model"),"checks":checks,"scores":scores,"passed":all(checks.values()),"seconds":round(time.monotonic()-started,2),"limitations":["三个固定文本的真实模型冒烟检查，不代表检索准确率"]}
    (ROOT/"outputs/reranker-smoke.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"passed":report["passed"],"scores":scores,"seconds":report["seconds"]}),flush=True)
    return report["passed"]


if __name__=="__main__":
    try: raise SystemExit(not main())
    except Exception as exc:
        print("Local reranker verification stopped; error type="+type(exc).__name__)
        raise SystemExit(1)
