import asyncio
import json
from pathlib import Path

import pytest

from icp.engineering import reliability, fault_tree_probability, cuts_from_graph, minimal_cuts
from icp.graphs import graph_for
from icp.knowledge import Knowledge
from icp.tools import Tools
from scripts.evaluate import evaluate

ROOT=Path(__file__).resolve().parents[1]
CASES=json.loads((ROOT/"eval/core_cases.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("question", ["系统的边界如何定义", "设备的接口如何描述", "指标如何分解和验证"])
def test_documented_engineering_concepts_require_domain_evidence(question):
    from icp.engine import route
    assert route(question)=="Hybrid_Search"


@pytest.fixture(scope="session")
def kb():
    return Knowledge()


@pytest.mark.parametrize("case",CASES,ids=[c["id"] for c in CASES])
def test_core_cases(case,kb,monkeypatch):
    for key in ["LLM_MODEL","RAGFLOW_API_KEY","TOOL_BACKEND","DIFY_AGENT_API_KEY"]:
        monkeypatch.delenv(key,raising=False)
    asyncio.run(evaluate(case,kb,Tools(kb)))


@pytest.mark.parametrize("index",range(4))
@pytest.mark.parametrize("hours",[1,5,10,50])
def test_rbd_source_baselines(kb,index,hours):
    model=kb.by_kind["rbd"][index]
    fields=model["fields"]
    expected=next(row[1] for row in fields["expected"][1:] if float(row[0])==hours)
    assert f"{reliability(fields['expression'],fields['rates'],hours):.8f}"==expected


@pytest.mark.parametrize("index",range(8))
def test_fta_source_baselines(kb,index):
    fields=kb.by_kind["fta"][index]["fields"]
    value=fault_tree_probability({e["id"]:e["probability"] for e in fields["events"]},fields["minimal_cuts"])
    assert f"{value:.3e}"==f"{fields['source_probability']:.3e}"


def test_shared_event_absorption():
    # A OR (A AND B) == A, including when B is certain.
    assert fault_tree_probability({"A":.1,"B":1},[["A"],["A","B"]])==pytest.approx(.1)


def test_nonidentical_voting_units():
    value=reliability("KOFN(2,A,B,C)",{"A":.1,"B":.2,"C":.3},1)
    a,b,c=[__import__('math').exp(-x) for x in [.1,.2,.3]]
    assert value==pytest.approx(a*b*(1-c)+a*c*(1-b)+b*c*(1-a)+a*b*c)


def test_source_graphs_have_no_dangling_edges(kb):
    for graph in kb.data["graphs"]:
        ids={n["id"] for n in graph["nodes"]}
        assert all(e["source"] in ids and e["target"] in ids and e["evidence_ids"] for e in graph["edges"])


def test_entity_name_types(kb):
    expected={"TopLevelFunction","SubFunction","RBD_SystemSuccess","FTATopEvent","FTABasicEvent","FailureMode","FailureEffect"}
    assert {e["entity_type"] for e in kb.data["entity_chunks"]}==expected


@pytest.mark.parametrize("index",range(8))
def test_fta_graph_cutsets_match_original_tables(kb,index):
    model=kb.by_kind["fta"][index]
    graph=graph_for(kb,"fta",model["id"])
    assert cuts_from_graph(graph)==minimal_cuts(model["fields"]["minimal_cuts"])

