import asyncio
import sqlite3

import pytest

from icp import planner
from icp.engine import Engine
from icp.knowledge import Knowledge
from icp.storage import Store
from icp.tools import Tools
import icp.tools as tools_module
from scripts.backup_local import backup


def test_external_retrieval_rejects_foreign_versions(monkeypatch):
    kb=Knowledge()
    record=kb.records["91810001-**-0001"]
    eid=record["evidence_ids"][0]
    monkeypatch.setenv("RAGFLOW_API_KEY","test-only-placeholder")
    monkeypatch.setenv("RAGFLOW_SYNTHETIC_DATASET_IDS","test-dataset")
    async def search(*args):
        return {"chunks":[{"id":"wrong","content":f"[CORPUS_VERSION=old] [SOURCE_ID={eid}] incorrect"},
                          {"id":"current","content":f"[CORPUS_VERSION={kb.version}] [SOURCE_ID={eid}] current"}]}
    monkeypatch.setattr(tools_module,"ragflow_search",search)
    result=asyncio.run(Tools(kb).execute("Hybrid_Search",{"query":"输入调理故障"}))
    assert [c["id"] for c in result["data"]]==["current"]
    assert result["evidence"][0]["id"]==eid


def test_never_ending_model_hits_step_limit(monkeypatch):
    monkeypatch.delenv("TOOL_BACKEND",raising=False)
    monkeypatch.setattr(planner,"enabled",lambda:True)
    count=0
    async def choose(*args):
        nonlocal count
        count+=1
        return {"tool":"HMC_Query","query":f"91810001-**-0001，第{count}轮","corpus":"synthetic","missing_fields":[],"summary":"查询"}
    monkeypatch.setattr(planner,"choose",choose)
    engine=Engine(Tools(Knowledge()),Store("sqlite:///:memory:"))
    run=engine.new("查询91810001-**-0001","test-session")
    asyncio.run(engine.process(run))
    assert run["status"]=="needs_input" and len(run["trace"])<=12


def test_sqlite_backup_restores_to_new_file(tmp_path):
    source=tmp_path/"original.sqlite3"
    with sqlite3.connect(source) as db:
        db.execute("CREATE TABLE check_data (value TEXT)")
        db.execute("INSERT INTO check_data VALUES ('retained')")
    copy=tmp_path/"backup.sqlite3"
    restored=tmp_path/"restored.sqlite3"
    backup(source,copy)
    backup(copy,restored)
    with sqlite3.connect(restored) as db:
        assert db.execute("SELECT value FROM check_data").fetchone()[0]=="retained"
        assert db.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
    with pytest.raises(ValueError): backup(source,restored)
