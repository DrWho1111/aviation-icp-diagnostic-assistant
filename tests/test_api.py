import json

from fastapi.testclient import TestClient
import pytest

from icp.app import app


@pytest.fixture
def client(monkeypatch,tmp_path):
    monkeypatch.setenv("DATABASE_URL","sqlite:///"+(tmp_path/"sessions.db").as_posix())
    monkeypatch.setenv("READ_KB_FROM_POSTGRES","false")
    monkeypatch.setenv("GRAPH_BACKEND","local")
    for key in ["TOOL_BACKEND","LLM_MODEL","RAGFLOW_API_KEY","DIFY_AGENT_API_KEY"]:
        monkeypatch.delenv(key,raising=False)
    with TestClient(app) as client:
        yield client


def test_health_and_original_source(client):
    assert client.get("/api/health").json()["mode"]=="local_development"
    kb=client.get("/api/kb/status").json()
    assert kb["counts"]["hmc"]==100
    sid=kb["documents"][0]["id"]
    assert client.get("/api/sources/"+sid).status_code==200


def test_sse_full_run_can_be_resumed(client):
    response=client.post("/api/chat",json={"question":"查询HMC 91810001-**-0001","session_id":"api-test-session"})
    assert response.status_code==202
    rid=response.json()["run_id"]
    response=client.get(f"/api/runs/{rid}/events")
    assert response.status_code==200
    events=[json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    assert events[-1]["status"]=="completed"
    assert "91810001-**-0001" in events[-1]["answer"]
    assert events[-1]["evidence"]
    assert client.get("/api/runs/"+rid).json()["status"]=="completed"


def test_confirmation_api_rejects_forged_candidate(client):
    response=client.post("/api/chat",json={"question":"画面卡住但后台数据在更新，怎么排查？","session_id":"api-test-session"})
    rid=response.json()["run_id"]
    client.get(f"/api/runs/{rid}/events")
    run=client.get("/api/runs/"+rid).json()
    assert run["status"]=="needs_confirmation"
    payload={"function_id":"FUNC-999","confirmation_token":run["confirmation_token"],"corpus_version":run["corpus_version"],"session_id":"api-test-session"}
    assert client.post(f"/api/runs/{rid}/confirm",json=payload).status_code==409
    payload["function_id"]="FUNC-410"
    assert client.post(f"/api/runs/{rid}/confirm",json=payload).status_code==202
    response=client.get(f"/api/runs/{rid}/events")
    final=client.get("/api/runs/"+rid).json()
    assert final["status"]=="completed" and len(final["evidence"])>3
    assert len(final["trace"][1]["branches"])==4
    assert client.post(f"/api/runs/{rid}/confirm",json=payload).json()["reused_confirmation"] is True
    exported=client.get(f"/api/runs/{rid}/trace?format=json").json()
    assert len(exported["steps"][1]["Observation"]["data"]["branches"])==4
    assert all(not key.startswith("_") for step in exported["steps"] for key in step["Action"]["parameters"])


def test_internal_tools_require_private_credential(client):
    response=client.post("/internal/tools/HMC_Query",json={"input":"91810001-**-0001"})
    assert response.status_code==401


def test_answer_mode_is_validated_and_persisted_without_fake_citations(client,monkeypatch):
    async def model(*args,**kwargs):return '向量数据库按向量相似度查找内容。'
    monkeypatch.setattr('icp.engine.generate',model)
    body={'question':'什么是向量数据库','session_id':'answer-mode-api','answer_mode':'general'}
    response=client.post('/api/chat',json=body)
    assert response.status_code==202
    rid=response.json()['run_id'];client.get('/api/runs/'+rid+'/events')
    run=client.get('/api/runs/'+rid).json()
    assert run['status']=='completed' and run['answer_mode']=='general' and run['answer_basis']=='general_model'
    assert not run['evidence'] and not run['trace'] and '通用知识说明' in run['answer']
    assert client.post('/api/chat',json={**body,'answer_mode':'unrestricted'}).status_code==422


@pytest.mark.parametrize('status,expected_http',[('error',503),('not_found',200),('needs_input',200)])
def test_locator_dependency_error_is_retryable_but_material_states_are_not(client,monkeypatch,status,expected_http):
    monkeypatch.setenv('INTERNAL_TOOL_TOKEN','local-test-only-secret')
    async def execute(*args):
        return {'status':status,'data':[],'evidence':[],'missing_fields':[],'coverage':{},
                'error':'upstream temporarily unavailable' if status=='error' else None,'corpus_version':'test'}
    monkeypatch.setattr(app.state.tools,'execute',execute)
    r=client.post('/internal/tools/Function_Locator',json={'input':'网络接入与路由异常'},headers={'Authorization':'Bearer local-test-only-secret'})
    assert r.status_code==expected_http
    if status=='error':assert 'upstream' not in r.text and '暂不可用' in r.json()['detail']


def test_native_observation_accepts_bounded_branch_payload_but_still_validates_task(client,monkeypatch):
    monkeypatch.setenv('INTERNAL_TOOL_TOKEN','test-internal-token')
    response=client.post('/internal/react/observe',json={'input':json.dumps({'root_run_id':'missing','result':'x'*20000})},headers={'Authorization':'Bearer test-internal-token'})
    assert response.status_code==400
    assert client.post('/internal/react/observe',json={'input':'x'*512001},headers={'Authorization':'Bearer test-internal-token'}).status_code==422
    assert client.post('/internal/tools/HMC_Query',json={'input':'[]'},headers={'Authorization':'Bearer test-internal-token'}).status_code==400


def test_chat_rejects_blank_or_unbounded_input(client):
    assert client.post("/api/chat",json={"question":" ","session_id":"api-test-session"}).status_code==422
    assert client.post("/api/chat",json={"question":"x"*8001,"session_id":"api-test-session"}).status_code==422


def test_three_line_trace_retains_actual_tool_observations(client):
    run=client.post("/api/chat",json={"question":"查询HMC 91810001-**-0001","session_id":"trace-test-session"}).json()
    rid=run["run_id"]
    client.get(f"/api/runs/{rid}/events")
    text=client.get(f"/api/runs/{rid}/trace").text
    blocks=text.split("\n\n")
    assert blocks and all(len(block.splitlines())==3 for block in blocks)
    steps=client.get(f"/api/runs/{rid}/trace?format=json").json()["steps"]
    assert steps[0]["Action"]["tool"]=="HMC_Query"
    assert steps[0]["Observation"]["data"][0]["id"]=="91810001-**-0001"
    assert "<think>" not in text
    assert client.get(f"/api/runs/{rid}/trace?format=invalid").status_code==422


def test_trace_for_unknown_run_does_not_fabricate_steps(client):
    assert client.get("/api/runs/missing/trace").status_code==404


def saved_question(client,cid='conversation-one',owner='browser-one',question='查询HMC 91810001-**-0001'):
    created=client.post('/api/chat',json={'question':question,'session_id':cid,'client_id':owner})
    assert created.status_code==202
    rid=created.json()['run_id']
    client.get('/api/runs/'+rid+'/events')
    return client.get('/api/runs/'+rid).json()


def test_history_restores_all_questions_and_keeps_clients_separate(client):
    first=saved_question(client)
    second=saved_question(client,question='计算RBD-SYN-001的10小时可靠度')
    saved_question(client,cid='conversation-other',owner='browser-other')
    listing=client.get('/api/conversations?client_id=browser-one').json()['conversations']
    assert len(listing)==1 and listing[0]['count']==2
    assert 'confirmation_token' not in json.dumps(listing)
    restored=client.get('/api/conversations/conversation-one?client_id=browser-one').json()
    assert [r['id'] for r in restored['runs']]==[first['id'],second['id']]
    assert restored['runs'][0]['evidence']
    assert client.get('/api/conversations/conversation-one?client_id=browser-other').status_code==404


def test_history_rename_is_owned_and_validated(client):
    saved_question(client)
    assert client.patch('/api/conversations/conversation-one',json={'client_id':'browser-other','title':'其他人'}).status_code==404
    assert client.patch('/api/conversations/conversation-one',json={'client_id':'browser-one','title':'  '}).status_code==422
    assert client.patch('/api/conversations/conversation-one',json={'client_id':'browser-one','title':'显示故障复核'}).status_code==200
    assert client.get('/api/conversations?client_id=browser-one').json()['conversations'][0]['title']=='显示故障复核'


def test_history_export_excludes_confirmation_credentials(client):
    run=saved_question(client)
    exported=client.get('/api/conversations/conversation-one/export?client_id=browser-one')
    assert exported.status_code==200 and run['question'] in exported.text and run['answer'] in exported.text
    assert run['confirmation_token'] not in exported.text
    assert 'confirmation_token' not in exported.text
    assert client.get('/api/conversations/conversation-one/export?client_id=browser-other').status_code==404


def test_history_delete_is_scoped_and_removes_question_details(client):
    first=saved_question(client)
    other=saved_question(client,cid='conversation-other',owner='browser-other')
    assert client.request('DELETE','/api/conversations/conversation-one',json={'client_id':'browser-other'}).status_code==404
    deleted=client.request('DELETE','/api/conversations/conversation-one',json={'client_id':'browser-one'})
    assert deleted.json()=={'deleted':1}
    assert client.get('/api/runs/'+first['id']).status_code==404
    assert client.get('/api/runs/'+other['id']).status_code==200
    assert client.get('/api/kb/status').json()['counts']['hmc']==100


def test_history_clear_only_affects_own_conversations(client):
    saved_question(client)
    saved_question(client,cid='conversation-two')
    saved_question(client,cid='conversation-other',owner='browser-other')
    assert client.request('DELETE','/api/conversations',json={'client_id':'browser-one'}).json()=={'deleted':2}
    assert not client.get('/api/conversations?client_id=browser-one').json()['conversations']
    assert len(client.get('/api/conversations?client_id=browser-other').json()['conversations'])==1


def test_history_imports_legacy_without_claiming_other_session(client):
    created=client.post('/api/chat',json={'question':'查询HMC 91810001-**-0001','session_id':'legacy-browser'}).json()
    client.get('/api/runs/'+created['run_id']+'/events')
    client.post('/api/conversations/import-legacy',json={'client_id':'other-browser'})
    assert not client.get('/api/conversations?client_id=other-browser').json()['conversations']
    for _ in range(2):assert client.post('/api/conversations/import-legacy',json={'client_id':'legacy-browser'}).status_code==200
    assert len(client.get('/api/conversations?client_id=legacy-browser').json()['conversations'])==1
    assert client.post('/api/chat',json={'question':'查询PFL','session_id':'legacy-browser','client_id':'other-browser'}).status_code==404


def test_history_keeps_pending_confirmation_after_reopen(client):
    run=saved_question(client,question='画面卡住但后台数据在更新，怎么排查？')
    restored=client.get('/api/conversations/conversation-one?client_id=browser-one').json()['runs'][0]
    assert restored['status']=='needs_confirmation' and restored['candidates']==run['candidates']
    confirmed=client.post('/api/runs/'+run['id']+'/confirm',json={'function_id':'FUNC-410','confirmation_token':restored['confirmation_token'],'corpus_version':restored['corpus_version'],'session_id':'conversation-one'})
    assert confirmed.status_code==202
    client.get('/api/runs/'+run['id']+'/events')
    assert client.get('/api/conversations/conversation-one?client_id=browser-one').json()['runs'][0]['status']=='completed'


def test_history_rejects_deletion_while_task_active(client):
    run=saved_question(client)
    app.state.engine.active_runs[run['id']]='conversation-one'
    assert client.request('DELETE','/api/conversations',json={'client_id':'browser-one'}).status_code==409
    assert client.get('/api/runs/'+run['id']).status_code==200
    app.state.engine.active_runs.pop(run['id'])


def test_history_persists_across_store_reopen(client):
    from icp.storage import Store
    saved_question(client)
    reopened=Store(app.state.store.url)
    assert reopened.list_conversations('browser-one')[0]['count']==1
    assert reopened.conversation('conversation-one','browser-one')['runs'][0]['status']=='completed'
    reopened.engine.dispose()
