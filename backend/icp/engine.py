from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import re
import time
import uuid

from .integrations import IntegrationError, dify_tool, generate, generate_grounded
from .answering import readable_text
from .query_intent import constrain_action,diagnostic_intent,workspace_question,WORKSPACE_OVERVIEW
from .parameters import mission_hours
from .followup import resolve_followup
from .storage import Store
from .tools import Tools
from . import planner


def route(question: str) -> str:
    if any(w and w in question for w in os.getenv("SENSITIVE_DEMO_WORDS", "诊断演示敏感词").split(",")):
        return "sensitive"
    from .coverage_boundaries import unsupported_equipment_target
    if unsupported_equipment_target(question):return 'Hybrid_Search'
    if re.search(r"\d{8}|(?<![A-Za-z])HMC(?![A-Za-z])|健康管理码|故障代码|故障码", question, re.I):
        return "HMC_Query"
    if re.search(r"(?<![A-Za-z])(?:MFL|PFL)(?![A-Za-z])|维修清单|维护清单|Get\w+FaultStatus|API.*上报", question, re.I):
        return "MFL_PFL_Query"
    if re.search(r"RBD|可靠度|可靠性框图", question, re.I):
        return "RBD_Query"
    if re.search(r"FTA|故障树|割集|顶事件|底事件", question, re.I):
        return "FTA_Query"
    if re.search(r"HIS-SYN-|历史|案例|以前|发生过|统计", question):
        return "Fault_History_Query"
    if re.search(r"FMECA|FM-SYN-|影响|后果|危害|严重度|什么阶段|什么时机|何时发生|什么时候", question, re.I):
        return "FMECA_Query"
    if re.search(r"对应.*功能|定位.*功能|什么功能|哪个功能", question):
        return "Function_Locator"
    if diagnostic_intent(question):
        if re.search(r"ICP|系统|设备|模块|画面|电源|通道|总线|网络|风扇|温度|日志|故障|处理器|缓存|启动|时钟|显示", question, re.I):
            return "Fault_Reasoning_Engine"
    if re.search(r"ICP|综合处理机|系统|设备|模块|指标|可靠性|故障|功能|需求|性能|维修|保障|FPA|FPGA|FUNC-|MOD-|电源|日志|总线|通道|显示|重启|瞬断|温度|风扇|时钟|缓存|网关|处理器|告警|过压|欠压", question, re.I):
        return "Hybrid_Search"
    return "general"


def format_records(tool: str, result: dict, *, expand_fta: bool = True) -> str:
    status = result["status"]
    if result.get('coverage',{}).get('view')=='insufficient_evidence':return result['coverage']['explanation']
    if status == "needs_input":
        return "还需要补充：" + "、".join(result["missing_fields"]) + "。已有候选和来源列于下方。"
    if status == "not_covered":
        return "当前资料未覆盖此项。" + "；".join(str(v) for v in result["coverage"].values())
    if status == "not_found":
        return "当前知识库未找到相符记录。请核对编号、模块或补充现象。"
    if status == "error":
        return "查询未完成：" + (result["error"] or "工具执行失败")
    data = result["data"]
    if result.get('coverage',{}).get('view')=='policy':
        return '系统使用边界：'+result['coverage']['explanation']
    if result.get('coverage',{}).get('view')=='catalog':
        catalog=result['coverage'];label=catalog['label']
        text=f"当前 SYN-ICP-9000 资料共收录 {catalog['total']} 条{label}。"
        if catalog['matched']!=catalog['total']:text+=f"与你指定范围匹配的有 {catalog['matched']} 条。"
        parts=[text]
        if catalog['groups']:parts.append('按子系统分类：\n'+'\n'.join(f"- {g['name']}：{g['count']} 条" for g in catalog['groups']))
        if catalog.get('encoding'):parts.append('HMC用于标识和查询故障，编码由LCN、HAMC、HSC三段组成；**是本手册定义的合法HAMC通配段。')
        if data:parts.append(('完整清单：' if catalog['returned']==catalog['matched'] else f"先列出 {catalog['returned']} 条示例：")+'\n'+'\n'.join('- '+r['id']+' · '+r['name'] for r in data))
        parts.append('可以继续指定子系统、模块、故障现象或编号进行查询。')
        parts.append('依据：'+'、'.join(e['id'] for e in result['evidence'][:6]))
        return '\n\n'.join(parts)
    if tool in {"RBD_Query", "FTA_Query"} and isinstance(data, list) and data and "model" in data[0]:
        parts = []
        for entry in data:
            model = entry["model"]
            if "reliability" in entry:
                parts.append(f"{model['id']} {model['name']}\n任务时间：{entry['hours']:g}小时\n可靠度：{entry['reliability']:.8f}\n结构：{model['fields']['expression']}\n假设：{model['fields']['assumption']}\n来源：{', '.join(model['evidence_ids'])}")
            else:
                if not expand_fta:
                    parts.append(f"{model['id']} {model['name']}\n顶事件概率：{entry['probability']:.3e}\n最小割集：{len(entry['minimal_cuts'])}组；完整底事件及割集可按此模型编号查询\n方法：{entry['method']}\n假设：{entry['assumption']}\n来源：{', '.join(model['evidence_ids'])}")
                    continue
                events=model['fields']['events']
                labels={event['id']:event['module']+'：'+event['name'] for event in events}
                event_lines=['- '+event['id']+' · '+labels[event['id']]+f"；合成概率 {event['probability']:.6g}" for event in events]
                cut_lines=[str(index)+'. '+' ∧ '.join(event+'（'+labels.get(event,event)+'）' for event in cut)
                    for index,cut in enumerate(entry['minimal_cuts'],1)]
                parts.append(f"{model['id']} {model['name']}\n顶事件概率：{entry['probability']:.3e}\n底事件（{len(events)}项）：\n"+
                    '\n'.join(event_lines)+f"\n最小割集：{len(entry['minimal_cuts'])}组（组内事件同时发生；各组为可导致顶事件的组合）\n"+
                    '\n'.join(cut_lines)+f"\n方法：{entry['method']}\n假设：{entry['assumption']}\n来源：{', '.join(model['evidence_ids'])}")
        return "\n\n".join(parts)
    if not isinstance(data, list):
        return json.dumps(data, ensure_ascii=False, indent=2)
    parts = []
    for record in data[:6]:
        if "fields" not in record:
            parts.append(readable_text(record.get("content", ""))[:700])
            continue
        fields = record["fields"]
        header = f"{record['id']} · {record['name']}"
        kind = record["kind"]
        if kind == "hmc":
            body = "维修方法："+fields["maintenance"]
        elif kind == "fmeca":
            body = f"可能原因：{fields['cause']}\n任务阶段：{fields['stage']}\n局部影响：{fields['local_effect']}\n最终影响：{fields['final_effect']}\n严重度：{fields['severity']}；概率等级：{fields['probability_class']}（等级不是数值概率）\n检测：{fields['detection']}\n补偿：{fields['mitigation']}"
        elif kind == "mfl":
            body = f"可直接上报：{fields['direct_report']}\nAPI名称：{fields['api_name']}（仅文档说明）\n验证状态：{fields['validation_status']}\n维修处置：{fields['maintenance']}"
        elif kind == "history":
            body = f"该案例已确认原因：{fields['confirmed_cause']}\n该案例修复：{fields['solution']}\n相似案例不能证明本次故障根因。"
        else:
            body = fields.get("description", fields.get("content", fields.get("expression", json.dumps(fields, ensure_ascii=False))))
        parts.append(header+"\n"+body+"\n来源："+", ".join(record["evidence_ids"]))
    return "\n\n".join(parts)


class Engine:
    def __init__(self, tools: Tools, store: Store):
        self.tools, self.store = tools, store
        self.cache = {}
        self.tasks = set()
        self.active_runs = {}

    def new(self, question: str, session_id: str, answer_mode: str = 'auto'):
        run = {"id": uuid.uuid4().hex, "session_id": session_id, "question": question,
               "status": "queued", "created_at": time.time(), "corpus_version": self.tools.kb.version,
               "trace": [], "evidence": [], "candidates": [], "answer": "", "result": None,
               "confirmation_token": uuid.uuid4().hex, "selected_function_id": None, "error": None,
               'answer_mode':answer_mode}
        previous=self.store.latest_run(session_id)
        application_help,topic=workspace_question(question)
        context=resolve_followup(topic if application_help else question,previous,self.tools.kb) if topic else None
        # A reply to a persisted native parameter form belongs to that workflow.
        # Do not depend on a special RBD phrase to discover this relationship.
        private=self.store.workflow(previous['id']) if previous else None
        if (previous and previous['status']=='needs_input' and private
                and private.get('kind')=='native_main' and private.get('purpose')=='input'
                and private.get('phase')=='paused' and private.get('corpus_version')==run['corpus_version']
                and not re.match(r'^(?:换个问题|新问题|取消|不用了|重新开始)',question.strip())):
            combined=previous.get('effective_question',previous['question'])+'\n用户补充：'+question
            if len(combined)>8000:
                context={'context_unresolved':True,'context_note':'补参上下文超过长度限制，请新建诊断并合并填写问题和参数。'}
            else:
                context={**(context or {}),'context_source_run_id':previous['id'],
                    'effective_question':(context or {}).get('effective_question',combined),
                    'context_note':((context or {}).get('context_note','')+' 已将补充内容绑定到同一原生工作流。').strip()}
                context.pop('context_unresolved',None)
                for field in ['trace','evidence','observations','answer_parts']:
                    if field in previous:run[field]=copy.deepcopy(previous[field])
        if context: run.update(context)
        return self.store.save(run, create=True)

    def schedule(self, run: dict, confirmed=False):
        scheduled=time.perf_counter()
        async def measured():
            from .timing import TIMINGS
            timing=[];token=TIMINGS.set(timing)
            dispatch=round((time.perf_counter()-scheduled)*1000,3)
            try:await self.process(run,confirmed=confirmed)
            finally:
                TIMINGS.reset(token)
                saved=self.store.get(run['id'])
                if saved:
                    samples=saved.setdefault('measurements',[])
                    samples.append({'phase':'resume' if confirmed else 'start','dispatch_wait_ms':dispatch,'transport':timing,
                        'model_queue_ms':None,'note':'service wall time includes platform/model queueing; provider queue is not independently observable'})
                    self.store.save(saved)
        task = asyncio.create_task(measured())
        self.tasks.add(task)
        self.active_runs[run['id']]=run['session_id']
        def finished(completed):
            self.tasks.discard(completed)
            self.active_runs.pop(run['id'],None)
        task.add_done_callback(finished)

    async def call(self, run: dict, name: str, params: dict, *, internal=False):
        key = hashlib.sha256(json.dumps([name, params, run["corpus_version"]], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        start = time.perf_counter()
        cached = key in self.cache
        if cached:
            result = self.cache[key]
        else:
            try:
                if os.getenv("TOOL_BACKEND", "local") == "dify" and not internal:
                    result = await dify_tool(name, params, run["session_id"])
                    if not isinstance(result, dict) or "status" not in result:
                        raise IntegrationError("Dify工具结果缺少状态")
                else:
                    result = await self.tools.execute(name, params)
            except IntegrationError as exc:
                result=self.tools.result("error",error=str(exc))
            if result.get("corpus_version")!=run["corpus_version"] or any(not isinstance(e,dict) or e.get("id") not in self.tools.kb.data["evidence"] for e in result.get("evidence",[])):
                result=self.tools.result("error",error="工具结果的知识版本或来源不匹配，已拒绝合并")
            if result["status"] in {"ok", "not_found", "not_covered"}:
                self.cache[key] = result
        trace = {"id": uuid.uuid4().hex, "tool": name, "params": {k:v for k,v in params.items() if not k.startswith("_")},
                 "status": result["status"], "elapsed_ms": round((time.perf_counter()-start)*1000, 1), "cached": cached,
                 "summary": "复用同版本查询结果" if cached else "按当前问题查询对应资料",
                 "backend": "dify" if os.getenv("TOOL_BACKEND")=="dify" and not internal else "local"}
        trace["observation"] = {k: result.get(k) for k in (
            "status", "data", "evidence", "missing_fields", "coverage", "error", "corpus_version",
        )}
        if name == "Fault_Reasoning_Engine" and isinstance(result.get("data"),dict):
            trace["branches"] = [{"tool":n,"status":v["status"]} for n,v in result["data"].get("branches",{}).items()]
        result = {**result, "tool_call_id": trace["id"]}
        run["trace"].append(trace)
        run["evidence"] = list({e["id"]:e for e in run["evidence"]+result["evidence"]}.values())
        self.store.save(run)
        return result

    async def process(self, run: dict, *, confirmed=False):
        try:
            question=run.get("effective_question",run["question"])
            application_help,topic=workspace_question(question)
            if application_help and not topic:
                run.pop('context_unresolved',None);run.pop('context_note',None)
                run.update(status='completed',answer=WORKSPACE_OVERVIEW,generation='application_help')
                self.store.save(run)
                return
            if run.get("context_unresolved"):
                run.update(status="needs_input",answer=run["context_note"])
                self.store.save(run)
                return
            from .answer_scope import general_explanation
            if route(question)!='sensitive' and general_explanation(question,self.tools.kb,run.get('answer_mode','auto')):
                run.update(status='running',answer='正在由本地模型整理通用解释…',answer_basis='general_model',route='general')
                self.store.save(run)
                answer=await generate(question,{},general=True,background=True)
                run.update(status='completed',answer=('通用知识说明（本地 Qwen3）\n\n'+answer if answer else
                    '通用问答模型尚未就绪，请检查本地模型服务后重试。'),generation='llm' if answer else 'model_unavailable')
                self.store.save(run)
                return
            run["status"] = "running"
            self.store.save(run)
            baseline = route(question)
            if run.get('answer_mode')=='knowledge' and baseline=='general':
                baseline='Hybrid_Search'
            module_reference=any(re.search(r"(?<![A-Za-z0-9])"+re.escape(m["fields"]["alias"])+r"(?![A-Za-z0-9])",question,re.I) for m in self.tools.kb.by_kind["module"])
            named_reference=any(r['name'] in question for kind in ['function','module','subsystem'] for r in self.tools.kb.by_kind[kind])
            if baseline=="general" and (module_reference or named_reference):
                baseline='Fault_Reasoning_Engine' if diagnostic_intent(question) else 'Hybrid_Search'
            from . import native_agent
            native_private=self.store.workflow(run['id']) or {}
            if native_agent.enabled() and baseline not in {'general','sensitive'} and (not confirmed or native_private.get('kind')=='native_main'):
                run['route']=baseline
                self.store.save(run)
                await native_agent.execute(self,run,confirmed=confirmed)
                return
            selected = "Fault_Reasoning_Engine" if confirmed else baseline
            action = None
            using_planner = planner.enabled() and baseline not in {"sensitive","general"}
            if using_planner and not confirmed:
                action = constrain_action(question,await planner.choose(question,run.get("observations",[]),run["session_id"]),self.tools.kb)
                if module_reference and re.search(r'是什么|什么意思|含义|定义',question) and baseline=='Hybrid_Search':
                    action={**action,'tool':'Hybrid_Search','query':question,'corpus':'synthetic','missing_fields':[]}
                run["planner_mode"] = "dify" if os.getenv("DIFY_AGENT_API_KEY") else "local_llm"
                if action["missing_fields"]:
                    run.update(status="needs_input",answer="还需要补充："+"、".join(action["missing_fields"]))
                    self.store.save(run)
                    return
                selected = action["tool"] if action["tool"] != "End" else baseline
                if action["tool"]=="End": action["query"]=question
                if selected=="Fault_Reasoning_Engine" and not diagnostic_intent(question):
                    selected=baseline
                if selected=="Function_Locator" and re.search(r"含义|是什么意思|有什么影响",question):
                    selected=baseline
            else:
                run["planner_mode"] = "rule_baseline" if not using_planner else run.get("planner_mode","llm")
            if not using_planner and not confirmed and baseline not in {"sensitive","general"}:
                action=constrain_action(question,{"tool":baseline,"query":question,"corpus":"synthetic","missing_fields":[]},self.tools.kb)
                selected=action["tool"]
            run["route"] = selected
            query=action["query"] if action else question
            if selected=="RBD_Query" and re.search(r"计算|可靠度",query):
                try: hours=mission_hours(query)
                except (ValueError,TypeError,OverflowError):
                    run.update(status="needs_input",answer="请提供单个非负、有限的任务时间，可使用小时、分钟或天；不能使用负数或时间范围。")
                    self.store.save(run)
                    return
                if hours is None:
                    run.update(status="needs_input",answer="请补充任务时间（小时），再计算可靠度。")
                    self.store.save(run)
                    return
            if selected == "sensitive":
                run.update(answer="该问题触发已配置的演示敏感规则，无法回答。", status="completed")
            elif selected == "general":
                answer = await generate(question, {}, general=True)
                run.update(answer=answer or "你好！这里是航空ICP知识库工作台。通用模型尚未连接；你可以先查询合成系统中的编码、功能、影响和工程模型。", status="completed", generation="llm" if answer else "template",answer_basis='general_model')
            elif selected == "Fault_Reasoning_Engine":
                from . import native_diagnosis
                if native_diagnosis.enabled():
                    await native_diagnosis.execute(self,run,confirmed=confirmed)
                    return
                if not confirmed:
                    result = await self.call(run, "Fault_Reasoning_Engine", {"query": question})
                    if result["status"]=="error":
                        run.update(status="error",answer="功能定位服务查询失败，请检查平台状态。",error=result["error"],result=result)
                    elif not result["data"]:
                        run.update(status="needs_input", answer="未找到可确认的功能。请补充模块、故障码或更具体的现象。", result=result)
                    else:
                        run.update(status="needs_confirmation", candidates=result["data"], result=result,
                                   answer="请先确认本次异常对应的功能，再查询故障树、失效影响、可靠性结构和历史案例。")
                else:
                    fid = run["selected_function_id"]
                    function = self.tools.kb.records[fid]
                    # Four internal branches, while the outer controller performs one Engine action.
                    using_dify = os.getenv("TOOL_BACKEND")=="dify"
                    result = await self.call(run, "Fault_Reasoning_Engine", {"query": question, "confirmed_function_id": fid,
                        "_confirmation_validated": True, "_run_id":run["id"], "_confirmation_token":run["confirmation_token"], "_session_id":run["session_id"]}, internal=not using_dify)
                    run["result"] = result
                    if result["status"]!="ok":
                        run.update(status="error",error=result["error"],answer="多源诊断查询未完成，请检查服务状态。")
                        self.store.save(run)
                        return
                    branches = result["data"]["branches"]
                    fmeca = branches["FMECA_Query"]
                    history = branches["Fault_History_Query"]
                    pieces = [f"已确认功能：{function['name']}（{fid}）", "以下为合成资料支持的候选原因，尚未确认本次根因。",
                              "可能原因、影响与检测\n"+format_records("FMECA_Query", fmeca),
                              "相似历史案例\n"+format_records("Fault_History_Query", history)]
                    for name, label in [("FTA_Query", "故障逻辑"), ("RBD_Query", "成功结构")]:
                        branch = branches[name]
                        if branch["status"] == "ok":
                            pieces.append(label+"："+"；".join(r.get("model",r)["name"] for r in branch["data"]))
                        elif branch["status"] == "not_covered":
                            pieces.append(label+"：当前资料未覆盖此功能。")
                        else:
                            pieces.append(label+"：本路查询未完成，状态为"+branch["status"]+"。")
                    pieces.append("建议排查顺序：先核对故障发生阶段、完整HMC和现场状态；按上述检测方法区分候选；必要时查对应HMC／MFL处置。尚需本次观测数据，不能根据历史案例直接认定故障已确认。")
                    run.update(answer="\n\n".join(pieces), status="completed", generation="template")
            else:
                params = {"query": query}
                if selected == "Hybrid_Search" and re.search(r"是什么|原理|研究|架构|综述|区别", question):
                    params["corpus"] = "research"
                if selected=="Hybrid_Search" and action:
                    params["corpus"]=action["corpus"]
                result = await self.call(run, selected, params)
                run.update(result=result, answer=format_records(selected, result),
                           status="needs_input" if result["status"]=="needs_input" else "error" if result["status"]=="error" else "completed", generation="template")
            if run["status"]=="completed" and selected not in {"general","sensitive"}:
                observations=run.setdefault("observations",[])
                answers=run.setdefault("answer_parts",[])
                observations.append({"tool":selected,"status":run["result"]["status"],"summary":run["answer"][:3500]})
                answers.append(run["answer"])
                if using_planner:
                    ended=False
                    while len(run["trace"])<11:
                        next_action=await planner.choose(question,observations,run["session_id"])
                        if next_action["missing_fields"]:
                            run.update(status="needs_input",answer="还需要补充："+"、".join(next_action["missing_fields"]))
                            break
                        name=next_action["tool"]
                        if name=="End":
                            ended=True
                            break
                        params={"query":next_action["query"]}
                        if name=="Hybrid_Search": params["corpus"]=next_action["corpus"]
                        if any(t["tool"]==name and t["params"]==params for t in run["trace"]):
                            run["model_note"]="模型请求重复工具参数；已复用已有证据并结束"
                            ended=True
                            break
                        if name=="Fault_Reasoning_Engine":
                            if run.get("selected_function_id"):
                                ended=True
                                break
                            if not diagnostic_intent(question):
                                raise IntegrationError("规划选择了与当前问题不适用的故障引擎")
                            result=await self.call(run,name,params)
                            run.update(status="needs_confirmation",candidates=result["data"],result=result,
                                       answer="请确认当前异常对应的功能，再继续多源诊断。")
                            if not result["data"]: run.update(status="needs_input",answer="未定位到候选功能，请补充现象或模块。")
                            break
                        if name=="RBD_Query" and re.search(r"计算|可靠度",params["query"]):
                            try:hours=mission_hours(params['query'])
                            except (ValueError,TypeError,OverflowError):
                                run.update(status='needs_input',answer='请提供单个非负、有限的任务时间，可使用小时、分钟或天；不能使用负数或时间范围。')
                                break
                            if hours is None:
                                run.update(status="needs_input",answer="请补充任务时间（小时、分钟或天）。")
                                break
                        result=await self.call(run,name,params)
                        text=format_records(name,result)
                        run["result"]=result
                        observations.append({"tool":name,"status":result["status"],"summary":text[:3500]})
                        answers.append(text)
                        if result["status"] in {"needs_input","error"}:
                            run.update(status="needs_input" if result["status"]=="needs_input" else "error",answer=text)
                            break
                    if run["status"]=="completed" and not ended:
                        run.update(status="needs_input",answer="已达到本任务的12步执行限制，请缩小问题范围或补充明确参数。")
                if run["status"]=="completed": run["answer"]="\n\n".join(answers)
            await self.enrich_answer(run,selected,question)
            if run["status"]=="completed" and selected not in {"general", "sensitive"}:
                await self.call(run,"End",{"query":run["answer"]})
            self.store.save(run)
        except Exception as exc:
            run.update(status="error", error=str(exc) if isinstance(exc, IntegrationError) else "任务执行失败，已保留过程；请查看服务端诊断记录", answer="本次任务未完成，请检查依赖服务状态。")
            self.store.save(run)

    async def enrich_answer(self,run,selected,question):
        application_help,topic=workspace_question(question)
        await self._enrich_knowledge_answer(run,selected,topic if application_help and topic else question)
        if application_help and run['status']=='completed':
            run['answer']=WORKSPACE_OVERVIEW+'\n\n'+run['answer']

    async def _enrich_knowledge_answer(self,run,selected,question):
        # Open questions use a bounded, structured answer packet. Exact codes,
        # engineering values and confirmed diagnostic conclusions stay deterministic.
        result=run.get('result') or {}
        view=result.get('coverage',{}).get('view')
        # Counts and returned list membership are already exact tool results.
        # Regenerating them can claim 12 examples while listing only 8.
        if view in {'catalog','policy'}:return
        # A declared material gap is an authoritative tool outcome. Do not
        # replace it with a generic request for information from the user.
        if result.get('status')=='not_covered' and not run.get('evidence'):return
        summarize=selected=='Hybrid_Search' or selected=='FMECA_Query' and not re.search(r'FM(?:ECA)?-SYN-',question,re.I)
        observations=run.get('observations',[])
        hybrid_only=bool(observations) and all(o['tool']=='Hybrid_Search' for o in observations)
        if run['status']=='completed' and hybrid_only and not run['evidence']:
            run.update(answer='本次检索没有找到可引用的资料。请明确设备、模块名称或资料范围。',generation='insufficient_evidence')
            return
        eligible=(len(observations)==1 and result.get('status')=='ok') or (hybrid_only and bool(run['evidence']))
        if run['status']=='completed' and summarize and eligible:
            from .retrieval_boundaries import definition_term,term_in_sources
            term=definition_term(question)
            if selected=='Hybrid_Search' and view not in {'catalog','comparison','policy'} and term and run['evidence'] and not term_in_sources(term,run['evidence']):
                run.update(answer='本次检索未找到足以直接解释“'+term+'”的材料，暂不将相关段落拼成定义。请补充术语、指定文档，或在右侧核对候选资料。',generation='insufficient_evidence')
                return
            try:
                summary=json.dumps(observations,ensure_ascii=False) if hybrid_only else run['answer']
                if view=='comparison':
                    summary=json.dumps({'types':result['coverage']['topics'],'records':[
                        {'type':r['kind'],'id':r['id'],'name':r['name'],'fields':list(r['fields'])}
                        for r in result['data']]},ensure_ascii=False)
                evidence=run['evidence']
                if selected=='Hybrid_Search' and term:
                    evidence=sorted(evidence,key=lambda e:not term_in_sources(term,[e]))
                generated=await generate_grounded(question,evidence,summary)
                if generated:
                    run.update(answer=generated['answer'],generation='llm' if generated['sufficient'] else 'insufficient_evidence')
                elif selected=='Hybrid_Search':
                    raise IntegrationError('未取得经过证据校验的模型答复')
            except IntegrationError:
                run['model_note']='模型整理未通过检查，已保留可读的工具结果；未显示原始索引内容'
                if selected=='Hybrid_Search':
                    from .retrieval_boundaries import module_subject_records
                    modules,support=module_subject_records(self.tools.kb,term)
                    if not modules:modules=[r for r in self.tools.kb.by_kind['module'] if re.search(r'(?<![A-Za-z0-9])'+re.escape(r['fields']['alias'])+r'(?![A-Za-z0-9])',question,re.I)]
                    if modules and re.search('是什么|什么是|解释|介绍|含义|作用|干什么|做什么|有什么用|负责什么',question):
                        run.update(answer='按本系统资料说明：\n'+'\n'.join('- '+r['fields']['alias']+'（'+r['name']+'）：'+r['fields']['description'] for r in modules)+
                            '\n\n资料未明确给出的缩写英文全称不补写。依据：'+'、'.join(dict.fromkeys(eid for r in modules for eid in r['evidence_ids'])),generation='source_fallback')
                        run['evidence']=list({e['id']:e for e in run['evidence']+self.tools.kb.evidences([eid for r in modules for eid in r['evidence_ids']])}.values())
                    else:run.update(answer='这次检索未形成可可靠引用的完整答复。请补充具体系统、功能或所需资料范围；你也可以查看右侧候选来源核对。',generation='evidence_fallback')

    def confirm(self, rid: str, token: str, fid: str, version: str, session_id: str):
        with self.store.lock:
            run = self.store.get(rid)
            if not run or run["session_id"] != session_id:
                raise ValueError("任务不存在")
            if version != run["corpus_version"] or version != self.tools.kb.version or token != run["confirmation_token"]:
                raise ValueError("确认已失效，请重新定位功能")
            if fid not in {r["id"] for r in run["candidates"]}:
                raise ValueError("所选功能不属于当前候选集合")
            if run["selected_function_id"]:
                if run["selected_function_id"] != fid:
                    raise ValueError("当前任务已确认其他功能")
                return run, False
            if run["status"] != "needs_confirmation":
                raise ValueError("任务当前不等待确认")
            run.update(selected_function_id=fid, status="queued")
            self.store.save(run)
            return run, True

