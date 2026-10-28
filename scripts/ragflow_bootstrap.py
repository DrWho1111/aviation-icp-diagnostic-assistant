"""Initialize this project's RAGFlow account and local embedding provider."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys

from dotenv import dotenv_values
import httpx

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.configure_platforms import replace_env
from scripts.pull_platform_images import docker_cli


class RAGConsole:
    def __init__(self):
        self.client=httpx.Client(base_url=os.getenv('ICP_RAGFLOW_URL','http://127.0.0.1:9380').rstrip('/')+'/api/v1',timeout=120,trust_env=False)

    def request(self,method,path,**kwargs):
        response=self.client.request(method,path,**kwargs)
        try: value=response.json()
        except ValueError: value={"code":"invalid_json"}
        if response.status_code>=400 or value.get("code")!=0:
            (ROOT/".secrets/ragflow-last-error.json").write_text(json.dumps({"path":path,"http":response.status_code,"body":value},ensure_ascii=False),encoding="utf-8")
            raise RuntimeError("RAGFlow request failed; details retained privately")
        return value.get("data")

    def login(self):
        private=ROOT/".secrets/ragflow-admin.json"
        fresh=not private.exists()
        if fresh:
            private.write_text(json.dumps({"nickname":"ICPProject","email":"icp-project@example.com","password":"Icp@"+secrets.token_hex(16)},indent=2),encoding="utf-8")
        account=json.loads(private.read_text(encoding="utf-8"))
        code="import sys,base64;from pathlib import Path;from Cryptodome.PublicKey import RSA;from Cryptodome.Cipher import PKCS1_v1_5;r=RSA.import_key(Path('/ragflow/conf/public.pem').read_bytes());print(base64.b64encode(PKCS1_v1_5.new(r).encrypt(base64.b64encode(sys.stdin.read().encode()))).decode())"
        encrypted=subprocess.run([docker_cli(),"exec","-i",os.getenv('ICP_RAGFLOW_CONTAINER','icp-ragflow-ragflow-cpu-1'),"/ragflow/.venv/bin/python","-c",code],input=account["password"],capture_output=True,text=True,encoding="utf-8",timeout=30)
        if encrypted.returncode: raise RuntimeError("RAGFlow password encryption helper failed")
        payload={**account,"password":encrypted.stdout.strip()}
        response=self.client.post("/users" if fresh else "/auth/login",json=payload)
        value=response.json()
        if value.get("code")!=0:
            # A prior interruption can leave an account file before registration.
            if not fresh and "not registered" in str(value.get("message","")):
                response=self.client.post("/users",json=payload);value=response.json()
            if value.get("code")!=0:
                (ROOT/".secrets/ragflow-last-error.json").write_text(json.dumps(value),encoding="utf-8")
                raise RuntimeError("RAGFlow local account authentication failed")
        auth=response.headers.get("Authorization")
        if not auth: raise RuntimeError("RAGFlow authentication header missing")
        self.client.headers["Authorization"]=auth
        print("RAGFlow project account authenticated; no credentials displayed.",flush=True)

    def provider(self,name,instance,base_url,models):
        providers=self.request("GET","/providers")
        if not any(p.get("provider_name",p.get("name"))==name for p in providers):
            self.request("PUT","/providers",json={"provider_name":name})
        instances=self.request("GET",f"/providers/{name}/instances")
        if not any(i.get("instance_name")==instance for i in instances):
            self.request("POST",f"/providers/{name}/instances",json={"instance_name":instance,"api_key":"","base_url":base_url,"region":"default","model_info":models})
        return self.request("GET",f"/providers/{name}/instances/{instance}/models")


def main(reranker=False,graph_templates=False,graph_profile=False):
    console=RAGConsole()
    try:
        console.login()
        private=ROOT/".secrets/ragflow-api.json"
        if not private.exists(): private.write_text(json.dumps(console.request("POST","/system/tokens")),encoding="utf-8")
        token=json.loads(private.read_text(encoding="utf-8"))["token"]
        models=console.provider("Ollama","icp-local","http://host.docker.internal:11434",[
            {"model_name":"bge-m3:latest","model_type":["embedding"],"max_tokens":8192},
            {"model_name":"qwen3:8b","model_type":["chat"],"max_tokens":8192,"extra":{"thinking":False}}])
        (ROOT/".secrets/ragflow-models.json").write_text(json.dumps(models),encoding="utf-8")
        for model_type,model_name in [("chat","qwen3:8b"),("embedding","bge-m3:latest")]:
            console.request("PATCH","/models/default",json={"model_type":model_type,"model_provider":"Ollama","model_instance":"icp-local","model_name":model_name})
        changes={"RAGFLOW_API_KEY":token,"RAGFLOW_EMBEDDING_MODEL":"bge-m3:latest@Ollama"}
        if reranker:
            rerank=console.provider("HuggingFace","icp-reranker","http://host.docker.internal:9390",[
                {"model_name":"BAAI/bge-reranker-v2-m3","model_type":["rerank"],"max_tokens":512}])
            (ROOT/".secrets/ragflow-reranker.json").write_text(json.dumps(rerank),encoding="utf-8")
            changes["RAGFLOW_RERANK_ID"]="BAAI/bge-reranker-v2-m3@HuggingFace"
        if graph_profile:
            from scripts.graph_model_profile import apply_profile
            graph_profile_result=apply_profile();graph_name=graph_profile_result['name']
            local_models=console.request("GET","/providers/Ollama/instances/icp-local/models")
            if not any(m.get("name")==graph_name for m in local_models):
                console.request("POST","/providers/Ollama/instances/icp-local/models",json={"model_name":graph_name,"model_type":["chat"],"max_tokens":8192,"extra":{"thinking":False}})
            console.request("PATCH","/models/default",json={"model_type":"chat","model_provider":"Ollama","model_instance":"icp-local","model_name":graph_name})
            print("Project Graph model profile configured; existing Qwen3 weights reused.",flush=True)
        env=ROOT/".env";env.write_text(replace_env(env.read_text(encoding="utf-8"),changes),encoding="utf-8")
        if graph_templates:
            groups=json.loads((ROOT/"deploy/ragflow-graph-templates.json").read_text(encoding="utf-8"))["groups"]
            existing=console.request("GET","/compilation-template-groups",params={"page_size":100}).get("groups",[])
            ids={g["name"]:g["id"] for g in existing}
            for group in groups:
                payload={k:v for k,v in group.items() if k in {"name","description","templates"}}
                if group["name"] not in ids:
                    value=console.request("POST","/compilation-template-groups",json=payload)
                    ids[group["name"]]=value["id"]
                else:
                    saved=console.request("GET","/compilation-template-groups/"+ids[group["name"]])
                    comparable=[{k:t.get(k) for k in ["name","kind","config"]} for t in saved.get("templates",[])]
                    if comparable!=payload["templates"]:
                        console.request("PUT","/compilation-template-groups/"+ids[group["name"]],json=payload)
                print("RAGFlow Graph template available: "+group["name"],flush=True)
            (ROOT/".secrets/ragflow-graph-templates.json").write_text(json.dumps(ids),encoding="utf-8")
        print("RAGFlow local provider configuration saved; indexing and retrieval checks are separate.",flush=True)
    finally: console.client.close()


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--reranker",action="store_true");parser.add_argument("--graph-templates",action="store_true");parser.add_argument("--graph-profile",action="store_true")
    args=parser.parse_args()
    try: main(args.reranker,args.graph_templates,args.graph_profile)
    except Exception as exc:
        print("RAGFlow bootstrap stopped; error type="+type(exc).__name__)
        raise SystemExit(1)
