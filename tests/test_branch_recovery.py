import asyncio
from collections import Counter

from icp.integrations import IntegrationError
from icp.knowledge import Knowledge
from icp.tools import Tools


def diagnose(runner):
    return asyncio.run(Tools(Knowledge(), branch_runner=runner).execute(
        "Fault_Reasoning_Engine", {"query": "画面卡住", "confirmed_function_id": "FUNC-410",
                                   "_confirmation_validated": True}))


def test_transient_branch_failure_retries_only_the_failed_branch():
    calls = Counter()
    local = Tools(Knowledge())
    async def runner(name, params):
        calls[name] += 1
        if name == "FMECA_Query" and calls[name] == 1:
            raise IntegrationError("private provider response must never be exposed")
        return await local.execute(name, params)
    result = diagnose(runner)
    assert result["status"] == "ok" and len(result["data"]["branches"]) == 4
    assert calls == {"FTA_Query": 1, "FMECA_Query": 2, "RBD_Query": 1, "Fault_History_Query": 1}
    assert result["data"]["branches"]["FMECA_Query"]["coverage"]["workflow_retries"] == 1


def test_persistent_branch_failure_retains_other_evidence_and_stops_success():
    calls = Counter()
    local = Tools(Knowledge())
    async def runner(name, params):
        calls[name] += 1
        if name == "FMECA_Query":
            raise IntegrationError("private provider response must never be exposed")
        return await local.execute(name, params)
    result = diagnose(runner)
    assert result["status"] == "error" and calls["FMECA_Query"] == 2
    assert "FMECA_Query" in result["error"] and "private provider" not in str(result)
    assert result["data"]["branches"]["FTA_Query"]["status"] == "ok"
    assert result["evidence"]
