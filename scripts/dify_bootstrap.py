"""Initialize the local Dify project and import tool workflows; credentials stay private."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import time

from dotenv import dotenv_values
import httpx
import yaml

ROOT=Path(__file__).resolve().parents[1]
OLLAMA_PLUGIN="langgenius/ollama:1.0.2@ef203a332d1b2b8952dbaa012be70be3e0a2d576b001489fb3b070efc970ffbe"


class DifyConsole:
    def __init__(self):
        settings=dotenv_values(ROOT/".secrets/platforms/dify/.env")
        origin=os.getenv('ICP_DIFY_CONSOLE_URL') or settings.get("CONSOLE_API_URL") or "http://127.0.0.1:8081"
        self.client=httpx.Client(base_url=origin.rstrip("/")+"/console/api",timeout=120,trust_env=False)

    def request(self, method, path, **kwargs):
        for cookie in self.client.cookies.jar:
            if "csrf" in cookie.name.lower():
                self.client.headers["X-CSRF-Token"]=cookie.value
        response=self.client.request(method,path,**kwargs)
        if response.status_code>=400:
            private=ROOT/".secrets/dify-last-error.json"
            private.write_text(json.dumps({"path":path,"status":response.status_code,"body":response.text},ensure_ascii=False),encoding="utf-8")
            raise RuntimeError("Dify console request failed; status="+str(response.status_code)+"; private details retained")
        return response.json()

    def login(self):
        path=ROOT/".secrets/dify-admin.json"
        if not path.exists():
            path.write_text(json.dumps({"email":"icp-admin@example.com","name":"ICP Project",
                                        "password":"Icp@"+secrets.token_hex(16)},indent=2),encoding="utf-8")
        account=json.loads(path.read_text(encoding="utf-8"))
        status=self.request("GET","/setup")
        if status.get("step")=="not_started":
            settings=dotenv_values(ROOT/".secrets/platforms/dify/.env")
            initial=self.request("GET","/init")
            if initial.get("status")!="finished":
                self.request("POST","/init",json={"password":settings["INIT_PASSWORD"]})
            self.request("POST","/setup",json={**account,"language":"zh-Hans"})
        encoded=base64.b64encode(account["password"].encode()).decode()
        value=self.request("POST","/login",json={"email":account["email"],"password":encoded,"remember_me":True})
        if value.get("result")!="success": raise RuntimeError("Dify local account login did not succeed")
        print("Dify local project account authenticated; no credentials displayed.",flush=True)

    def import_tool(self, name, document):
        result=self.request("POST","/apps/imports",json={"mode":"yaml-content","name":name,
                                                           "yaml_content":yaml.safe_dump(document,allow_unicode=True,sort_keys=False)})
        if result.get("status")=="pending":
            result=self.request("POST","/apps/imports/"+result["id"]+"/confirm",json={})
        if result.get("status") not in {"completed","completed-with-warnings","completed_with_warnings"}:
            raise RuntimeError("Dify import did not complete")
        app_id=result["app_id"]
        self.request("POST",f"/apps/{app_id}/workflows/publish",json={"marked_name":"ICP core"})
        key=self.request("POST",f"/apps/{app_id}/api-keys",json={})
        return {"app_id":app_id,"api_key":key["token"]}

    def install_provider(self):
        installed=self.request("GET","/workspaces/current/model-providers").get("data",[])
        provider="langgenius/ollama/ollama"
        if not any(p.get("provider")==provider for p in installed):
            package=ROOT/".tools/dify-plugins/ollama-1.0.2.difypkg"
            if not package.exists():
                package.parent.mkdir(parents=True,exist_ok=True)
                with httpx.Client(follow_redirects=True,timeout=120,trust_env=True) as downloader:
                    response=downloader.get("https://marketplace.dify.ai/api/v1/plugins/download-url",params={"unique_identifier":OLLAMA_PLUGIN})
                    response.raise_for_status()
                    content=response.content
                if hashlib.sha256(content).hexdigest()!="d5c96814544da173edaeefc6c692904a57b71d950558e8735677a081bd9e9c8f":
                    raise RuntimeError("Pinned Ollama package digest mismatch")
                package.write_bytes(content)
            if hashlib.sha256(package.read_bytes()).hexdigest()!="d5c96814544da173edaeefc6c692904a57b71d950558e8735677a081bd9e9c8f":
                raise RuntimeError("Pinned Ollama package digest mismatch")
            with package.open("rb") as handle:
                self.request("POST","/workspaces/current/plugin/upload/pkg",files={"pkg":(package.name,handle,"application/octet-stream")})
            task=self.request("POST","/workspaces/current/plugin/install/pkg",json={"plugin_unique_identifiers":[OLLAMA_PLUGIN]})
            deadline=time.monotonic()+300
            while time.monotonic()<deadline:
                # Reading a terminal installation task invalidates Dify's
                # tenant provider cache. Polling only model-providers can keep
                # returning the empty list cached before installation.
                if task.get("task_id"):
                    status=self.request("GET","/workspaces/current/plugin/tasks/"+task["task_id"]).get("task",{})
                    if status.get("status")=="failed":
                        raise RuntimeError("Ollama plugin installation failed; private platform details retained")
                installed=self.request("GET","/workspaces/current/model-providers").get("data",[])
                if any(p.get("provider")==provider for p in installed): break
                time.sleep(5)
            else: raise RuntimeError("Ollama plugin initialization timed out; inspect sanitized platform logs")
        return provider

    def configure_model(self):
        provider=self.install_provider()
        path="/workspaces/current/model-providers/"+provider+"/models"
        models=self.request("GET",path).get("data",[])
        if not any(m.get("model")=="qwen3:8b" for m in models):
            self.request("POST",path+"/credentials",json={"model":"qwen3:8b","model_type":"llm","name":"Local Qwen3 8B",
                "credentials":{"base_url":"http://host.docker.internal:11434","mode":"chat","context_size":"8192",
                               "max_tokens":"2048","vision_support":"false","function_call_support":"true"}})
        print("Dify Ollama provider and local Qwen3 model configured.",flush=True)
        private=ROOT/".secrets/dify-main.json"
        if not private.exists():
            main=yaml.safe_load((ROOT/"deploy/ICP_Main_Agent.yml").read_text(encoding="utf-8"))
            private.write_text(json.dumps(self.import_tool("ICP_Main_Agent",main),indent=2),encoding="utf-8")
        print("Published main Chatflow is available; no credentials displayed.",flush=True)


def main(import_tools=False,configure_model=False,install_provider=False):
    private=ROOT/".secrets";private.mkdir(exist_ok=True)
    console=DifyConsole()
    try:
        console.login()
        if import_tools:
            state_path=private/"dify-apps.json"
            state=json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
            settings=dotenv_values(ROOT/".env")
            token=settings.get("INTERNAL_TOOL_TOKEN","")
            if not token: raise RuntimeError("Internal tool credential is not configured")
            for source in sorted((ROOT/"deploy/dify-tools").glob("*.yml")):
                name=source.stem
                if name in state:
                    print(json.dumps({"tool":name,"status":"existing_import_retained"}),flush=True);continue
                document=yaml.safe_load(source.read_text(encoding="utf-8"))
                for env in document["workflow"]["environment_variables"]:
                    if env["name"]=="INTERNAL_TOOL_TOKEN": env["value"]=token
                    if env["name"]=="TOOL_SERVICE_URL": env["value"]=os.getenv('ICP_TOOL_SERVICE_URL','http://host.docker.internal:8000')
                state[name]=console.import_tool(name,document)
                state_path.write_text(json.dumps(state,indent=2),encoding="utf-8")
                print(json.dumps({"tool":name,"status":"imported_and_published"}),flush=True)
            (private/"dify-tools.json").write_text(json.dumps({k:v["api_key"] for k,v in state.items()},indent=2),encoding="utf-8")
        if install_provider:console.install_provider();print('Pinned Ollama provider installed; no inference test requested.',flush=True)
        if configure_model: console.configure_model()
        print("Dify bootstrap completed; live workflow checks are separate.",flush=True)
    finally:
        console.client.close()


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--import-tools",action="store_true");parser.add_argument("--configure-model",action="store_true");parser.add_argument('--install-provider',action='store_true')
    args=parser.parse_args()
    try: main(args.import_tools,args.configure_model,args.install_provider)
    except Exception as exc:
        print("Dify bootstrap stopped. Error type="+type(exc).__name__+"; private state retained.")
        raise SystemExit(1)
