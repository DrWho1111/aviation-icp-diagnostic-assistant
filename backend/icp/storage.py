from __future__ import annotations

import json
import os
from pathlib import Path
import threading
import time

from sqlalchemy import Column, Float, Integer, JSON, MetaData, String, Table, create_engine, delete, insert, select, update

ROOT = Path(__file__).resolve().parents[2]


class Store:
    def __init__(self, url: str | None = None):
        (ROOT/"data/runtime").mkdir(parents=True, exist_ok=True)
        self.url = url or os.getenv("DATABASE_URL", "sqlite:///"+(ROOT/"data/runtime/sessions.sqlite3").as_posix())
        self.engine = create_engine(self.url, connect_args={"check_same_thread": False} if self.url.startswith("sqlite") else {})
        self.lock = threading.RLock()
        metadata = MetaData()
        self.runs = Table("diagnostic_runs", metadata, Column("id", String(48), primary_key=True),
                          Column("status", String(32), nullable=False), Column("revision", Integer, nullable=False), Column("payload", JSON, nullable=False))
        self.conversations = Table('diagnostic_conversations', metadata,
            Column('id', String(100), primary_key=True), Column('client_id', String(100), nullable=False, index=True),
            Column('title', String(120), nullable=False), Column('created_at', Float, nullable=False), Column('updated_at', Float, nullable=False))
        self.private_workflows=Table('diagnostic_private_workflows',metadata,
            Column('run_id',String(48),primary_key=True),Column('payload',JSON,nullable=False))
        metadata.create_all(self.engine)

    def workflow(self,rid):
        with self.engine.connect() as connection:
            return connection.execute(select(self.private_workflows.c.payload).where(self.private_workflows.c.run_id==rid)).scalar_one_or_none()

    def save_workflow(self,rid,payload):
        with self.lock,self.engine.begin() as connection:
            exists=connection.execute(select(self.private_workflows.c.run_id).where(self.private_workflows.c.run_id==rid)).first()
            if exists:connection.execute(update(self.private_workflows).where(self.private_workflows.c.run_id==rid).values(payload=payload))
            else:connection.execute(insert(self.private_workflows).values(run_id=rid,payload=payload))

    def get(self, rid: str):
        with self.engine.connect() as connection:
            row = connection.execute(select(self.runs.c.payload).where(self.runs.c.id == rid)).first()
            return row[0] if row else None

    def latest_run(self, session_id):
        with self.engine.connect() as connection:
            return connection.execute(select(self.runs.c.payload).where(
                self.runs.c.payload['session_id'].as_string()==session_id).order_by(
                self.runs.c.payload['created_at'].as_float().desc()).limit(1)).scalar_one_or_none()

    def save(self, run: dict, *, create=False):
        run["revision"] = run.get("revision", 0)+1
        # Round-trip ensures later in-memory mutation cannot modify a persisted snapshot.
        payload = json.loads(json.dumps(run, ensure_ascii=False))
        with self.engine.begin() as connection:
            if create:
                connection.execute(insert(self.runs).values(id=run["id"], status=run["status"], revision=run["revision"], payload=payload))
            else:
                connection.execute(update(self.runs).where(self.runs.c.id == run["id"]).values(status=run["status"], revision=run["revision"], payload=payload))
            connection.execute(update(self.conversations).where(self.conversations.c.id==run['session_id']).values(updated_at=time.time()))
        return run

    def ensure_conversation(self, cid, client_id, title):
        with self.engine.begin() as connection:
            row=connection.execute(select(self.conversations).where(self.conversations.c.id==cid)).mappings().first()
            if row:
                if row['client_id']!=client_id:raise KeyError('对话不存在')
            else:
                legacy=connection.execute(select(self.runs.c.id).where(self.runs.c.payload['session_id'].as_string()==cid).limit(1)).first()
                if legacy and cid!=client_id:raise KeyError('对话不存在')
                now=time.time()
                connection.execute(insert(self.conversations).values(id=cid,client_id=client_id,title=title[:120],created_at=now,updated_at=now))

    def adopt_legacy(self, client_id):
        with self.engine.connect() as connection:
            row=connection.execute(select(self.runs.c.id).where(self.runs.c.payload['session_id'].as_string()==client_id).limit(1)).first()
        if row:self.ensure_conversation(client_id,client_id,'此前诊断记录')

    def conversation_owner(self,cid):
        with self.engine.connect() as connection:
            return connection.execute(select(self.conversations.c.client_id).where(self.conversations.c.id==cid)).scalar_one_or_none()

    def conversation(self,cid,client_id):
        with self.engine.connect() as connection:
            row=connection.execute(select(self.conversations).where(self.conversations.c.id==cid,self.conversations.c.client_id==client_id)).mappings().first()
            if not row:raise KeyError('对话不存在')
            runs=connection.execute(select(self.runs.c.payload).where(self.runs.c.payload['session_id'].as_string()==cid)).scalars().all()
        return {**dict(row),'runs':sorted(runs,key=lambda r:(r['created_at'],r['id']))}

    def list_conversations(self,client_id):
        with self.engine.connect() as connection:
            rows=[dict(r) for r in connection.execute(select(self.conversations).where(self.conversations.c.client_id==client_id).order_by(self.conversations.c.updated_at.desc())).mappings()]
            if not rows:return []
            messages=connection.execute(select(self.runs.c.payload['session_id'].as_string(),self.runs.c.payload['question'].as_string(),self.runs.c.status,self.runs.c.payload['created_at'].as_float()).where(self.runs.c.payload['session_id'].as_string().in_([r['id'] for r in rows]))).all()
        grouped={r['id']:[] for r in rows}
        for cid,question,status,created in messages:grouped[cid].append((created,question,status))
        for row in rows:
            items=sorted(grouped[row['id']]);row['count']=len(items)
            row['preview']=items[-1][1][:100] if items else ''
            row['status']='running' if any(i[2] in {'queued','running'} for i in items) else items[-1][2] if items else 'empty'
            row.pop('client_id')
        return rows

    def rename_conversation(self,cid,client_id,title):
        with self.engine.begin() as connection:
            result=connection.execute(update(self.conversations).where(self.conversations.c.id==cid,self.conversations.c.client_id==client_id).values(title=title,updated_at=time.time()))
            if not result.rowcount:raise KeyError('对话不存在')

    def delete_conversations(self,client_id,cid=None):
        with self.engine.begin() as connection:
            query=select(self.conversations.c.id).where(self.conversations.c.client_id==client_id)
            if cid:query=query.where(self.conversations.c.id==cid)
            ids=list(connection.execute(query.with_for_update()).scalars())
            if cid and not ids:raise KeyError('对话不存在')
            if not ids:return 0
            scope=self.runs.c.payload['session_id'].as_string().in_(ids)
            running=connection.execute(select(self.runs.c.id).where(scope,self.runs.c.status.in_(['queued','running'])).limit(1)).first()
            if running:raise ValueError('仍有任务正在运行，请等待完成后再删除')
            run_ids=select(self.runs.c.id).where(scope)
            connection.execute(delete(self.private_workflows).where(self.private_workflows.c.run_id.in_(run_ids)))
            connection.execute(delete(self.runs).where(scope))
            connection.execute(delete(self.conversations).where(self.conversations.c.id.in_(ids)))
            return len(ids)

    def recover_interrupted(self,*,recover_native=True):
        resumable=[]
        with self.engine.begin() as connection:
            rows = connection.execute(select(self.runs.c.id, self.runs.c.payload).where(self.runs.c.status.in_(["queued", "running"]))).all()
            for rid, payload in rows:
                private=connection.execute(select(self.private_workflows.c.payload).where(self.private_workflows.c.run_id==rid)).scalar_one_or_none()
                native_main=private and private.get('kind')=='native_main' and private.get('workflow_id') and private.get('phase') in {'running','resuming'}
                if recover_native and private and (native_main or private.get('phase') in {'paused','resuming'} and payload.get('selected_function_id')):
                    payload['status']='queued';resumable.append(payload)
                    continue
                payload.update(status="error", error="服务上次在执行中停止；已保留证据和过程，可重新提问", revision=payload["revision"]+1)
                connection.execute(update(self.runs).where(self.runs.c.id==rid).values(status="error", revision=payload["revision"], payload=payload))
        return resumable

