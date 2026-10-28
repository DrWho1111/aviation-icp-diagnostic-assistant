from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import secrets
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .engine import Engine, route
from .integrations import dify_tool
from .knowledge import Knowledge, ROOT
from .storage import Store
from .tools import Tools, TOOL_NAMES


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.kb = Knowledge(use_database=os.getenv("READ_KB_FROM_POSTGRES", "false").lower()=="true")
    app.state.store = Store()
    from .native_diagnosis import enabled as native_enabled
    from .native_agent import enabled as native_agent_enabled
    resumable=app.state.store.recover_interrupted(recover_native=native_enabled() or native_agent_enabled())
    async def branches(name,params):
        return await dify_tool(name,params,"internal-engine-branch")
    app.state.tools = Tools(app.state.kb, branch_runner=branches if os.getenv("TOOL_BACKEND")=="dify" else None)
    app.state.engine = Engine(app.state.tools, app.state.store)
    if native_enabled() or native_agent_enabled():
        for run in resumable:app.state.engine.schedule(run,confirmed=True)
    yield
    for task in list(app.state.engine.tasks):
        task.cancel()


app = FastAPI(title="航空 ICP 智能诊断工作台", version="0.1.0", lifespan=lifespan)


class Chat(BaseModel):
    question: str = Field(min_length=1, max_length=8000)
    session_id: str = Field(min_length=8, max_length=100)
    client_id: str | None = Field(default=None,min_length=8,max_length=100)
    answer_mode: Literal['auto','knowledge','general'] = 'auto'


class ConversationClient(BaseModel):
    client_id: str = Field(min_length=8,max_length=100)


class ConversationRename(ConversationClient):
    title: str = Field(min_length=1,max_length=120)


class Confirm(BaseModel):
    function_id: str
    confirmation_token: str
    corpus_version: str
    session_id: str


class ToolCall(BaseModel):
    input: str = Field(max_length=16000)


class NativeObservationCall(BaseModel):
    input: str = Field(max_length=512000)


@app.get("/api/health")
async def health():
    return {"status": "ok", "corpus_version": app.state.kb.version,
            "components": {"backend": "ready", "data": "ready",
                "storage": "postgresql" if app.state.store.url.startswith("postgres") else "sqlite_local",
                "dify": "configured_not_probed" if os.getenv("DIFY_TOOL_KEYS_FILE") else "not_configured",
                "ragflow": "configured_not_probed" if os.getenv("RAGFLOW_API_KEY") else "not_configured",
                "llm": "configured_not_probed" if os.getenv("LLM_MODEL") else "not_configured"},
            "mode": "platform" if os.getenv("TOOL_BACKEND")=="dify" and os.getenv("RAGFLOW_API_KEY") else "local_development",
            "model": os.getenv("LLM_MODEL") or "未连接", "synthetic": True}


@app.get("/api/kb/status")
async def kb_status():
    kb = app.state.kb
    value={k: kb.data[k] for k in ["version", "counts", "documents", "issues", "archive_sha256"]}
    review_path=ROOT/'data/processed/page_review.json'
    if review_path.is_file():
        review=json.loads(review_path.read_text(encoding='utf-8'))
        if review.get('corpus_version')==kb.version and review.get('archive_sha256')==kb.data['archive_sha256']:
            pages={(r['document_id'],r['page']):r for r in review.get('pages',[])}
            value['issues']=[{**issue,'review':pages.get((issue.get('document_id'),issue.get('page')))} for issue in kb.data['issues']]
    return value


@app.post("/api/chat", status_code=202)
async def chat(body: Chat):
    if not body.question.strip():
        raise HTTPException(422, "请输入问题")
    try:
        owner=app.state.store.conversation_owner(body.session_id)
        if owner and owner!=body.client_id:raise KeyError('对话不存在')
        if body.client_id:
            app.state.store.ensure_conversation(body.session_id,body.client_id,body.question.strip())
            existing=app.state.store.conversation(body.session_id,body.client_id)
            if any(r['status'] in {'queued','running'} for r in existing['runs']):raise ValueError('此对话仍有任务正在运行，请等待完成')
    except KeyError:raise HTTPException(404,'对话不存在') from None
    except ValueError as exc:raise HTTPException(409,str(exc)) from None
    run = app.state.engine.new(body.question.strip(), body.session_id,body.answer_mode)
    app.state.engine.schedule(run)
    return {"run_id":run["id"],"status":run["status"],"events_url":f"/api/runs/{run['id']}/events"}


@app.post('/api/conversations/import-legacy')
async def import_legacy(body: ConversationClient):
    app.state.store.adopt_legacy(body.client_id)
    return {'status':'ok'}


@app.get('/api/conversations')
async def list_conversations(client_id: str = Query(min_length=8,max_length=100)):
    return {'conversations':app.state.store.list_conversations(client_id)}


@app.get('/api/conversations/{cid}')
async def get_conversation(cid: str,client_id: str = Query(min_length=8,max_length=100)):
    try:return app.state.store.conversation(cid,client_id)
    except KeyError:raise HTTPException(404,'对话不存在') from None


@app.patch('/api/conversations/{cid}')
async def rename_conversation(cid: str,body: ConversationRename):
    if not body.title.strip():raise HTTPException(422,'请输入对话名称')
    try:app.state.store.rename_conversation(cid,body.client_id,body.title.strip())
    except KeyError:raise HTTPException(404,'对话不存在') from None
    return {'status':'ok'}


@app.delete('/api/conversations')
async def clear_conversations(body: ConversationClient):
    return await remove_conversations(body.client_id)


@app.delete('/api/conversations/{cid}')
async def delete_conversation(cid: str,body: ConversationClient):
    return await remove_conversations(body.client_id,cid)


async def remove_conversations(client_id,cid=None):
    owned={item['id'] for item in app.state.store.list_conversations(client_id)}
    scope=owned.intersection({cid}) if cid else owned
    if scope.intersection(app.state.engine.active_runs.values()):
        raise HTTPException(409,'仍有任务正在运行，请等待完成后再删除')
    try:return {'deleted':app.state.store.delete_conversations(client_id,cid)}
    except KeyError:raise HTTPException(404,'对话不存在') from None
    except ValueError as exc:raise HTTPException(409,str(exc)) from None


@app.get('/api/conversations/{cid}/export')
async def export_conversation(cid: str,client_id: str = Query(min_length=8,max_length=100)):
    try:conversation=app.state.store.conversation(cid,client_id)
    except KeyError:raise HTTPException(404,'对话不存在') from None
    blocks=['# '+conversation['title'],'导出包含问题、答复和引用，不包含私有确认凭据。']
    for number,run in enumerate(conversation['runs'],1):
        answer=run['answer'] or '任务尚未产生答复。'
        if run['status'] in {'queued','running'}:answer='任务仍在执行，尚未形成最终答复。请完成后重新导出。'
        blocks.extend([f'## 问答 {number}',run['question'],answer])
        if run['evidence']:blocks.append('引用：\n'+'\n'.join('- '+e['id']+' · '+e['document'] for e in run['evidence']))
    return PlainTextResponse('\n\n'.join(blocks))


@app.get("/api/runs/{rid}")
async def get_run(rid: str):
    run = app.state.store.get(rid)
    if not run:
        raise HTTPException(404, "任务不存在")
    return run


@app.post("/api/runs/{rid}/confirm", status_code=202)
async def confirm(rid: str, body: Confirm):
    try:
        run, start = app.state.engine.confirm(rid, body.confirmation_token, body.function_id, body.corpus_version, body.session_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    if start:
        app.state.engine.schedule(run, confirmed=True)
    return {"run_id":rid,"status":run["status"],"reused_confirmation":not start}


@app.get("/api/runs/{rid}/trace")
async def execution_trace(rid: str, format: Literal["text", "json"] = "text"):
    run = app.state.store.get(rid)
    if not run:
        raise HTTPException(404, "任务不存在")
    rows = []
    for step in run["trace"]:
        rows.append({
            "Thought": " ".join(step.get("summary", "执行当前相关查询").splitlines())[:100],
            "Action": {"tool": step["tool"], "parameters": step["params"]},
            "Observation": step.get("observation", {"status": step["status"], "note": "该历史步骤未记录完整工具返回"}),
        })
    if format == "json":
        return {"run_id": rid, "corpus_version": run["corpus_version"], "steps": rows,
                "summary_policy": "Thought为简短执行摘要，不包含模型内部思维链"}
    blocks = []
    for row in rows:
        action = row["Action"]
        blocks.append("Thought：" + row["Thought"] + "\nAction：" + action["tool"] + "[" +
                      json.dumps(action["parameters"], ensure_ascii=False) + "]\nObservation：" +
                      json.dumps(row["Observation"], ensure_ascii=False))
    return PlainTextResponse("\n\n".join(blocks))


@app.get("/api/runs/{rid}/events")
async def events(rid: str, request: Request, after: int = 0):
    if not app.state.store.get(rid):
        raise HTTPException(404, "任务不存在")
    last = max(after, int(request.headers.get("last-event-id", "0")) if request.headers.get("last-event-id", "0").isdigit() else 0)
    async def stream():
        nonlocal last
        for _ in range(1800):
            if await request.is_disconnected():
                break
            run = app.state.store.get(rid)
            if run["revision"] > last:
                last = run["revision"]
                yield f"id: {last}\nevent: update\ndata: {json.dumps(run,ensure_ascii=False)}\n\n"
            if run["status"] not in {"queued", "running"}:
                break
            await asyncio.sleep(.2)
        yield ": stream closed\n\n"
    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})


@app.get("/api/evidence/{eid}")
async def evidence(eid: str):
    found = app.state.kb.data["evidence"].get(eid)
    if not found:
        raise HTTPException(404, "证据不存在")
    return found


@app.get("/api/sources/{sid}")
async def source(sid: str):
    doc = next((d for d in app.state.kb.data["documents"] if d["id"]==sid), None)
    if not doc:
        raise HTTPException(404, "来源不存在")
    return FileResponse(ROOT/doc["path"], filename=doc["name"], content_disposition_type="inline")


@app.get("/api/assets/{sid}/{name}")
async def asset(sid: str, name: str):
    path = (ROOT/"data/processed/assets"/sid/name).resolve()
    root = (ROOT/"data/processed/assets").resolve()
    if root not in path.parents or path.suffix.lower() not in {".png", ".jpg", ".jpeg"} or not path.is_file():
        raise HTTPException(404, "图片不存在")
    return FileResponse(path)


@app.get("/api/graphs/{kind}/{rid}")
async def graph(kind: str, rid: str):
    from .graphs import graph_for
    try:
        from .graph_schema import typed_graph
        projection=typed_graph(app.state.kb,graph_for(app.state.kb,kind,rid))
        if os.getenv("GRAPH_BACKEND")=="neo4j":
            from .graph_store import read_graph
            return await asyncio.to_thread(read_graph,projection)
        return projection
    except KeyError:
        raise HTTPException(404, "图谱不存在") from None
    except RuntimeError:
        raise HTTPException(503,"Neo4j图谱未就绪；请检查当前版本发布状态") from None


@app.get("/api/records/{kind}")
async def records(kind: str):
    if kind not in app.state.kb.by_kind:
        raise HTTPException(404, "资料类型不存在")
    return app.state.kb.by_kind[kind]


@app.get("/api/evaluations")
async def evaluations():
    path = ROOT/"outputs/evaluation.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"status":"not_run","cases":[],"summary":{}}


@app.post("/internal/tools/{name}")
async def internal_tool(name: str, body: ToolCall, request: Request):
    expected = os.getenv("INTERNAL_TOOL_TOKEN", "")
    provided = request.headers.get("authorization", "").removeprefix("Bearer ")
    if not expected or not secrets.compare_digest(expected, provided):
        raise HTTPException(401, "内部工具认证失败")
    try:
        params = json.loads(body.input)
    except ValueError:
        params = {"query":body.input}
    if not isinstance(params,dict):
        raise HTTPException(400,'工具输入必须是查询文本或JSON对象')
    if isinstance(params, dict):
        params.pop("_confirmation_validated", None)
        if name=="Fault_Reasoning_Engine" and params.get("confirmed_function_id"):
            run=app.state.store.get(str(params.get("_run_id","")))
            if (run and run.get("selected_function_id")==params["confirmed_function_id"]
                    and run.get("confirmation_token")==params.get("_confirmation_token")
                    and run.get("session_id")==params.get("_session_id")
                    and run.get("corpus_version")==app.state.kb.version):
                params["_confirmation_validated"]=True
    # The logical action owner handles retry; nested retrieval must not multiply it.
    from .integrations import TRANSPORT_ATTEMPTS
    token=TRANSPORT_ATTEMPTS.set(1)
    from .timing import TIMINGS
    measured=[];timing_token=TIMINGS.set(measured)
    try:
        value = await app.state.tools.execute(name, params)
        if measured:value={**value,'measurements':measured}
    finally:
        TRANSPORT_ATTEMPTS.reset(token)
        TIMINGS.reset(timing_token)
    if name=='Function_Locator' and value.get('status')=='error':
        # The Dify HTTP node owns one retry. A successful HTTP envelope would
        # hide a dependency failure from that retry policy.
        raise HTTPException(503,'功能定位依赖服务暂不可用，请稍后重试')
    # Dify HTTP node consumes a string result through a following code node.
    return {"result":json.dumps(value, ensure_ascii=False)}


@app.post('/internal/react/{operation}')
async def native_react_step(operation: Literal['plan','execute','observe','continue'],body:NativeObservationCall,request:Request):
    expected=os.getenv('INTERNAL_TOOL_TOKEN','')
    provided=request.headers.get('authorization','').removeprefix('Bearer ')
    if not expected or not secrets.compare_digest(expected,provided):raise HTTPException(401,'内部控制认证失败')
    from .react_steps import operate
    from .integrations import TRANSPORT_ATTEMPTS,IntegrationError
    token=TRANSPORT_ATTEMPTS.set(1)
    try:
        payload=json.loads(body.input)
        root=payload['root_run_id']
        if not isinstance(root,str) or len(root)>48:raise ValueError('Invalid run')
        locks=getattr(app.state.engine,'native_step_locks',None)
        if locks is None:locks={};app.state.engine.native_step_locks=locks
        async with locks.setdefault(root,asyncio.Lock()):
            from .timing import TIMINGS
            measured=[];timing_token=TIMINGS.set(measured)
            try:result=await operate(app.state.engine,operation,payload)
            finally:TIMINGS.reset(timing_token)
            from .react_steps import current_run
            saved=current_run(app.state.engine,root)
            if measured:
                saved.setdefault('measurements',[]).append({'operation':operation,'transport':measured})
                app.state.store.save(saved)
        return {'result':json.dumps(result,ensure_ascii=False)}
    except (ValueError,KeyError,TypeError,IntegrationError):
        raise HTTPException(400,'原生循环操作未通过校验，请核对任务状态及平台服务') from None
    finally:TRANSPORT_ATTEMPTS.reset(token)


frontend = ROOT/"frontend/dist"
if frontend.is_dir():
    app.mount("/", StaticFiles(directory=frontend, html=True), name="workbench")
