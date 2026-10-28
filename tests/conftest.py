"""Unit tests never inherit live deployment credentials or enabled integrations."""
import pytest


@pytest.fixture(autouse=True)
def isolated_unit_environment(monkeypatch):
    for key in ['LLM_MODEL','LLM_API_KEY','DIFY_AGENT_API_KEY','DIFY_TOOL_KEYS_FILE',
        'DIFY_NATIVE_MAIN_KEYS_FILE','DIFY_NATIVE_DIAGNOSIS_KEYS_FILE','TOOL_BACKEND',
        'RAGFLOW_API_KEY','RAGFLOW_DATASET_IDS','RAGFLOW_SYNTHETIC_DATASET_IDS','RAGFLOW_RESEARCH_DATASET_IDS',
        'RAGFLOW_ENTITY_DATASET_IDS','RAGFLOW_FUNCTION_DOCUMENT_IDS','DATABASE_URL','READ_KB_FROM_POSTGRES','GRAPH_BACKEND']:
        monkeypatch.delenv(key,raising=False)
