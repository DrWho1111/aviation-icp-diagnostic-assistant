"""Generate Dify workflow DSL with secret placeholders, never actual API keys."""
from __future__ import annotations

import json
from pathlib import Path
import sys

import yaml

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"backend"))
from icp.tools import TOOL_NAMES
from icp.planner import PLANNER_PROMPT


def node(nid,typ,title,x,data):
    return {"id":nid,"type":"custom","position":{"x":x,"y":100},"positionAbsolute":{"x":x,"y":100},"width":244,"height":100,
            "data":{"type":typ,"title":title,"desc":"","selected":False,**data},"sourcePosition":"right","targetPosition":"left"}


def workflow(name):
    nodes=[node("start","start","输入",0,{"variables":[{"label":"input","variable":"input","type":"paragraph","required":True,"max_length":16000,"options":[]}]}),
        node("encode","code","安全编码请求",300,{"code_language":"python3","code":"import json\ndef main(input: str) -> dict:\n    return {'body': json.dumps({'input': input}, ensure_ascii=False)}\n",
             "variables":[{"variable":"input","value_selector":["start","input"]}],"outputs":{"body":{"type":"string","children":None}}}),
        node("request","http-request",name,600,{"method":"post","url":"{{#env.TOOL_SERVICE_URL#}}/internal/tools/"+name,
             "authorization":{"type":"no-auth","config":None},"headers":"Content-Type:application/json\nAuthorization:Bearer {{#env.INTERNAL_TOOL_TOKEN#}}",
             "params":"","body":{"type":"raw-text","data":[{"id":"body","key":"","type":"text","value":"{{#encode.body#}}"}]},
             "timeout":{"max_connect_timeout":10,"max_read_timeout":120,"max_write_timeout":30},
             "retry_config":{"retry_enabled":name=="Function_Locator","max_retries":1 if name=="Function_Locator" else 0,"retry_interval":200 if name=="Function_Locator" else 100},"variables":[]}),
        node("decode","code","校验返回",900,{"code_language":"python3","code":"import json\ndef main(body: str) -> dict:\n    value = json.loads(body)\n    result = value['result']\n    parsed = json.loads(result)\n    if 'status' not in parsed:\n        raise ValueError('Tool result missing status')\n    return {'result': result}\n",
             "variables":[{"variable":"body","value_selector":["request","body"]}],"outputs":{"result":{"type":"string","children":None}}}),
        node("end","end","输出工具结果",1200,{"outputs":[{"variable":"result","value_selector":["decode","result"]}]})]
    edges=[{"id":f"{a}-{b}","type":"custom","source":a,"sourceHandle":"source","target":b,"targetHandle":"target",
            "data":{"sourceType":nodes[i]["data"]["type"],"targetType":nodes[i+1]["data"]["type"],"isInIteration":False,"isInLoop":False}}
           for i,(a,b) in enumerate(zip(["start","encode","request","decode"],["encode","request","decode","end"]))]
    return {"app":{"name":name,"description":"SYN-ICP-9000合成资料工具；需完成平台导入与真实联调","mode":"workflow","icon":"🔎","icon_background":"#EFF6FF","use_icon_as_answer_icon":False},
            "dependencies":[],"kind":"app","version":"0.3.0","workflow":{"conversation_variables":[],
            "environment_variables":[{"id":"service-url","name":"TOOL_SERVICE_URL","description":"后端内部工具地址","selector":["env","TOOL_SERVICE_URL"],"value":"http://host.docker.internal:8000","value_type":"string"},
                                     {"id":"tool-token","name":"INTERNAL_TOOL_TOKEN","description":"从本机.env填写，禁止提交或截图","selector":["env","INTERNAL_TOOL_TOKEN"],"value":"","value_type":"secret"}],
            "features":{"file_upload":{"enabled":False},"opening_statement":"","suggested_questions":[],"suggested_questions_after_answer":{"enabled":False},"speech_to_text":{"enabled":False},"text_to_speech":{"enabled":False},"sensitive_word_avoidance":{"enabled":False}},
            "graph":{"nodes":nodes,"edges":edges,"viewport":{"x":0,"y":0,"zoom":.6}}}}


def main():
    target=ROOT/"deploy/dify-tools";target.mkdir(parents=True,exist_ok=True)
    for name in TOOL_NAMES:
        value=workflow(name)
        path=target/(name+".yml")
        path.write_text(yaml.safe_dump(value,allow_unicode=True,sort_keys=False),encoding="utf-8")
        loaded=yaml.safe_load(path.read_text(encoding="utf-8"))
        assert len(loaded["workflow"]["graph"]["nodes"])==5
        assert loaded["workflow"]["environment_variables"][1]["value"]==""
    main=workflow("ICP_Main_Agent")
    main["app"]["mode"]="advanced-chat"
    main["app"]["description"]="由后端逐轮调用的ReAct规划Chatflow；后端持久化确认和SSE状态，工具动作单步串行"
    main["workflow"]["environment_variables"]=[]
    nodes=[node("start","start","本轮已知结果",0,{"variables":[{"label":"context","variable":"context","type":"paragraph","required":False,"max_length":20000,"options":[]}]}),
           node("planner","llm","单工具规划",350,{"model":{"provider":"langgenius/ollama/ollama","name":"qwen3:8b","mode":"chat","completion_params":{"temperature":0,"num_predict":600,"num_ctx":8192,"think":False}},
                "context":{"enabled":False,"variable_selector":[]},"vision":{"enabled":False},"variables":[],
                "prompt_template":[{"id":"system","role":"system","text":PLANNER_PROMPT},{"id":"user","role":"user","text":"用户问题：{{#sys.query#}}\n已有工具结果：{{#start.context#}}\n/no_think"}]}),
           node("answer","answer","返回可验证决策",700,{"answer":"{{#planner.text#}}","variables":[]})]
    edges=[{"id":a+"-"+b,"type":"custom","source":a,"sourceHandle":"source","target":b,"targetHandle":"target","data":{"sourceType":source,"targetType":target,"isInIteration":False,"isInLoop":False}}
           for a,b,source,target in [("start","planner","start","llm"),("planner","answer","llm","answer")]]
    main["workflow"]["graph"]={"nodes":nodes,"edges":edges,"viewport":{"x":0,"y":0,"zoom":.8}}
    (ROOT/"deploy/ICP_Main_Agent.yml").write_text(yaml.safe_dump(main,allow_unicode=True,sort_keys=False),encoding="utf-8")
    print("Main Chatflow plus 10 tool DSL files generated. Local YAML checks only; actual Dify import is pending.")


if __name__=="__main__": main()
