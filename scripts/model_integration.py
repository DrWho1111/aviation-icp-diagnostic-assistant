"""Exercise the running app with the real local model; no mocked model responses."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import time
import uuid
from zoneinfo import ZoneInfo

import httpx

ROOT = Path(__file__).resolve().parents[1]
CASES = [
    {"id": "general", "question": "请用一句简短中文介绍你自己。", "status": "completed", "generation": "llm"},
    {"id": "hmc", "question": "查询HMC 91810001-**-0001的含义和维修方法", "status": "completed", "tool": "HMC_Query", "contains": "91810001-**-0001"},
    {"id": "rbd", "question": "计算RBD-SYN-001的10小时可靠度", "status": "completed", "tool": "RBD_Query", "contains": "0.99951007"},
    {"id": "missing_hours", "question": "计算RBD-SYN-001可靠度", "status": "needs_input", "contains": "时间"},
    {"id": "pfl", "question": "查询PFL清单", "status": "completed", "tool": "MFL_PFL_Query", "contains": "未提供"},
    {"id": "diagnosis", "question": "画面卡住但后台数据在更新，怎么排查？", "status": "needs_confirmation", "tool": "Fault_Reasoning_Engine"},
    {"id": "research", "question": "系统工程中的功能架构是什么？请根据研究资料解释并引用来源。", "status": "completed", "tool": "Hybrid_Search"},
]


def wait(client, rid):
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        run = client.get("/api/runs/" + rid).raise_for_status().json()
        if run["status"] not in {"queued", "running"}:
            return run
        time.sleep(.4)
    raise TimeoutError("Application run did not finish within 180 seconds")


def snapshot(run):
    return {key: run.get(key) for key in [
        "id", "question", "status", "planner_mode", "generation", "answer", "error", "trace", "evidence",
    ]}


def main(output,planner="local_llm",base_url="http://127.0.0.1:8000",fullstack=False):
    with httpx.Client(base_url=base_url, trust_env=False, timeout=15) as client:
        health = client.get("/api/health").raise_for_status().json()
        if health["model"] != "qwen3:8b":
            raise RuntimeError("Running application has not enabled qwen3:8b")
        if fullstack and planner != "dify":
            raise ValueError("Full stack verification requires the Dify planner")
        report = {"mode": "real_dify_ragflow_qwen" if fullstack else "real_dify_qwen" if planner=="dify" else "real_local_qwen", "date": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
                  "model": health["model"], "corpus_version": health["corpus_version"], "base_url":base_url,"cases": [],
                  "limitations": ["七个固定场景的冒烟检查，不代表模型准确率", "索引完整性与Graph另见ragflow-verification.json" if fullstack else "RAGFlow索引与检索须另外验收" if planner=="dify" else "未验证Dify/RAGFlow真实平台"]}
        for case in CASES:
            start = time.monotonic()
            item = {"id": case["id"], "question": case["question"], "passed": False}
            try:
                session = "live-qwen-" + uuid.uuid4().hex
                created = client.post("/api/chat", json={"question": case["question"], "session_id": session}).raise_for_status().json()
                run = wait(client, created["run_id"])
                item["initial"] = snapshot(run)
                checks = {"status": run["status"] == case["status"],
                          "no_thinking_in_answer": "<think>" not in run["answer"]}
                if case.get("tool"):
                    checks["tool"] = any(t["tool"] == case["tool"] for t in run["trace"])
                    checks["real_planner"] = run.get("planner_mode") == planner
                    if fullstack:
                        checks["dify_tool_execution"] = any(t["tool"] == case["tool"] and t.get("backend") == "dify" for t in run["trace"])
                if case.get("contains"):
                    checks["answer"] = case["contains"] in run["answer"]
                if case.get("generation"):
                    checks["generation"] = run.get("generation") == case["generation"]
                if case["id"] == "diagnosis":
                    checks["no_diagnostic_branches_before_confirmation"] = not any(t.get("branches") for t in run["trace"])
                    checks["candidate"] = any(c["id"] == "FUNC-410" for c in run["candidates"])
                    checks["three_candidates"] = len(run["candidates"]) == 3
                    if fullstack:
                        checks["ragflow_function_retrieval"] = any(t.get("observation",{}).get("coverage",{}).get("retrieval") == "ragflow_entity_hybrid" for t in run["trace"])
                    if run["status"] == "needs_confirmation" and checks["candidate"]:
                        client.post("/api/runs/" + run["id"] + "/confirm", json={
                            "function_id": "FUNC-410", "confirmation_token": run["confirmation_token"],
                            "corpus_version": run["corpus_version"], "session_id": session,
                        }).raise_for_status()
                        final = wait(client, run["id"])
                        item["confirmed"] = snapshot(final)
                        checks["confirmed_completed"] = final["status"] == "completed"
                        checks["four_branches"] = any(len(t.get("branches", [])) == 4 for t in final["trace"])
                        checks["source_and_uncertainty"] = bool(final["evidence"]) and "尚未确认本次根因" in final["answer"]
                if case["id"] == "research":
                    checks["research_corpus"] = any(t["tool"] == "Hybrid_Search" and t["params"].get("corpus") == "research" for t in run["trace"])
                    checks["evidence"] = bool(run["evidence"])
                    if fullstack:
                        checks["ragflow_hybrid_retrieval"] = any(t.get("observation",{}).get("coverage",{}).get("retrieval") == "ragflow_hybrid" for t in run["trace"])
                        checks["research_sources_only"] = bool(run["evidence"]) and all(e["corpus"] == "research" for e in run["evidence"])
                item.update(checks=checks, passed=all(checks.values()))
            except (httpx.HTTPError, TimeoutError, KeyError, ValueError) as exc:
                item["error"] = type(exc).__name__
            item["elapsed_seconds"] = round(time.monotonic() - start, 2)
            report["cases"].append(item)
            report["summary"] = {"total": len(CASES), "executed": len(report["cases"]),
                                 "passed": sum(c["passed"] for c in report["cases"])}
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps({"id": item["id"], "passed": item["passed"], "seconds": item["elapsed_seconds"]}), flush=True)
    return all(c["passed"] for c in report["cases"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "outputs/model-integration.json")
    parser.add_argument("--planner",choices=["local_llm","dify"],default="local_llm")
    parser.add_argument("--base-url",default="http://127.0.0.1:8000")
    parser.add_argument("--fullstack",action="store_true")
    args=parser.parse_args()
    raise SystemExit(not main(args.output,args.planner,args.base_url,args.fullstack))
