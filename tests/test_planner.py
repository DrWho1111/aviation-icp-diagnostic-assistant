import asyncio
import json

import pytest

from icp import planner
from icp.engine import Engine
from icp.integrations import IntegrationError
from icp.knowledge import Knowledge
from icp.storage import Store
from icp.tools import Tools


def test_unregistered_model_action_is_rejected(monkeypatch):
    monkeypatch.setenv("LLM_API_STYLE", "openai")
    monkeypatch.setenv("LLM_BASE_URL","http://localhost/unused")
    monkeypatch.setenv("LLM_MODEL","test-model")
    monkeypatch.delenv("DIFY_AGENT_API_KEY",raising=False)
    async def response(*args,**kwargs):
        return {"choices":[{"message":{"content":json.dumps({"tool":"RunShell","query":"do something"})}}]}
    monkeypatch.setattr(planner,"request_json",response)
    with pytest.raises(IntegrationError):
        asyncio.run(planner.choose("ICP问题",[],"test-session"))


@pytest.mark.parametrize("tool,accepted", [("End", True), ("Hybrid_Search", False)])
def test_empty_corpus_is_optional_only_for_end(monkeypatch, tool, accepted):
    monkeypatch.setenv("LLM_API_STYLE", "openai")
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost/unused")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.delenv("DIFY_AGENT_API_KEY", raising=False)
    async def response(*args, **kwargs):
        return {"choices": [{"message": {"content": json.dumps({
            "tool": tool, "query": "查询PFL清单", "corpus": "", "missing_fields": [],
        })}}]}
    monkeypatch.setattr(planner, "request_json", response)
    if accepted:
        decision = asyncio.run(planner.choose("查询PFL清单", [], "test-session"))
        assert decision["tool"] == "End" and decision["corpus"] == "synthetic"
    else:
        with pytest.raises(IntegrationError):
            asyncio.run(planner.choose("查询PFL清单", [], "test-session"))


def test_model_missing_fields_pause_without_tool(monkeypatch):
    monkeypatch.setattr(planner,"enabled",lambda:True)
    async def choose(*args):
        return {"tool":"RBD_Query","query":"计算RBD-SYN-001可靠度","corpus":"synthetic","missing_fields":["任务时间"],"summary":"缺少时间"}
    monkeypatch.setattr(planner,"choose",choose)
    engine=Engine(Tools(Knowledge()),Store("sqlite:///:memory:"))
    run=engine.new("计算RBD-SYN-001可靠度","test-session")
    asyncio.run(engine.process(run))
    assert run["status"]=="needs_input" and not run["trace"]


def test_native_ollama_disables_thinking_for_planning_and_answers(monkeypatch):
    import icp.integrations as integrations
    monkeypatch.setenv("LLM_API_STYLE", "ollama")
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("LLM_MODEL", "qwen3:8b")
    monkeypatch.delenv("DIFY_AGENT_API_KEY", raising=False)
    calls=[]
    async def response(method, url, **kwargs):
        body=kwargs["body"]
        calls.append((url, body))
        content=json.dumps({"tool":"End","query":"完成","corpus":"synthetic","missing_fields":[]}) if body.get("format")=="json" else "中文回答"
        return {"message":{"content":content,"thinking":"must never be returned"}}
    monkeypatch.setattr(planner, "request_json", response)
    monkeypatch.setattr(integrations, "request_json", response)
    assert asyncio.run(planner.choose("查询PFL清单", [], "test-session"))["tool"]=="End"
    assert asyncio.run(integrations.generate("你好", {}, general=True))=="中文回答"
    assert all(url.endswith("/api/chat") and body["think"] is False for url,body in calls)


def test_planner_cannot_drop_explicit_rbd_hours(monkeypatch):
    monkeypatch.setenv("LLM_API_STYLE", "openai")
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost/unused")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.delenv("DIFY_AGENT_API_KEY", raising=False)
    async def response(*args,**kwargs):
        return {"choices":[{"message":{"content":json.dumps({"tool":"RBD_Query","query":"RBD-SYN-001","missing_fields":["任务时间"]})}}]}
    monkeypatch.setattr(planner,"request_json",response)
    question="计算RBD-SYN-001的10小时可靠度"
    action=asyncio.run(planner.choose(question,[],"test-session"))
    assert action["query"]==question and not action["missing_fields"]


def test_model_loop_executes_only_serial_relevant_actions(monkeypatch):
    monkeypatch.delenv("TOOL_BACKEND",raising=False)
    monkeypatch.setattr(planner,"enabled",lambda:True)
    actions=iter([
        {"tool":"HMC_Query","query":"91810001-**-0001"},
        {"tool":"MFL_PFL_Query","query":"MFL-SYN-001"},
        {"tool":"End","query":"完成"}])
    async def choose(*args):
        return {"corpus":"synthetic","missing_fields":[],"summary":"查询有关记录",**next(actions)}
    monkeypatch.setattr(planner,"choose",choose)
    engine=Engine(Tools(Knowledge()),Store("sqlite:///:memory:"))
    run=engine.new("查询91810001-**-0001以及MFL-SYN-001","test-session")
    asyncio.run(engine.process(run))
    assert run["status"]=="completed"
    assert [t["tool"] for t in run["trace"]]==["HMC_Query","MFL_PFL_Query","End"]
    assert "91810001-**-0001" in run["answer"] and "MFL-SYN-001" in run["answer"]


def test_repeated_model_action_does_not_repeat_tool(monkeypatch):
    monkeypatch.delenv("TOOL_BACKEND",raising=False)
    monkeypatch.setattr(planner,"enabled",lambda:True)
    async def choose(*args):
        return {"tool":"HMC_Query","query":"91810001-**-0001","corpus":"synthetic","missing_fields":[],"summary":"查询"}
    monkeypatch.setattr(planner,"choose",choose)
    engine=Engine(Tools(Knowledge()),Store("sqlite:///:memory:"))
    run=engine.new("查询91810001-**-0001","test-session")
    asyncio.run(engine.process(run))
    assert run["status"]=="completed"
    assert [t["tool"] for t in run["trace"]]==["HMC_Query","End"]
