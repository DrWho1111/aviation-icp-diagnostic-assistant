from __future__ import annotations

import asyncio
import json
import os
import re

from .engineering import fault_tree_probability, minimal_cuts, reliability
from .integrations import IntegrationError, ragflow_search
from .knowledge import Knowledge
from .query_intent import catalog_kind, topics, LABELS, TOOLS
from .parameters import mission_hours
from .record_scope import scope_records, record_domain, DOMAINS
from .coverage_boundaries import asks_equipment_requirements,asks_history_proof,HISTORY_PROOF_POLICY,unsupported_equipment_target
from .retrieval_boundaries import navigation_only,definition_term,term_in_sources,module_subject_records

TOOL_NAMES = ["Hybrid_Search", "Function_Locator", "HMC_Query", "MFL_PFL_Query", "FMECA_Query", "FTA_Query", "RBD_Query", "Fault_History_Query", "Fault_Reasoning_Engine", "End"]


class Tools:
    def __init__(self, kb: Knowledge, branch_runner=None):
        self.kb = kb
        self.branch_runner = branch_runner

    def result(self, status="ok", data=None, *, records=None, missing=None, coverage=None, error=None):
        ids = [e for r in records or [] for e in r.get("evidence_ids", [])]
        return {"status": status, "data": data if data is not None else records or [],
                "evidence": self.kb.evidences(ids), "missing_fields": missing or [],
                "coverage": coverage or {}, "error": error, "corpus_version": self.kb.version}

    async def execute(self, name: str, params: dict | str) -> dict:
        if isinstance(params, str):
            try:
                params = json.loads(params)
            except ValueError:
                params = {"query": params}
        if not isinstance(params, dict):
            return self.result("error", error="工具参数必须为对象或问题字符串")
        if name not in TOOL_NAMES:
            return self.result("error", error="未知工具")
        if not isinstance(params.get('query',''),str):return self.result('error',error='query必须为问题字符串')
        query = params.get('query','').strip()
        if len(query) > 8000:
            return self.result("error", error="问题长度超过限制")
        if params.get("corpus_version", self.kb.version) != self.kb.version:
            return self.result("needs_input", missing=["当前知识库版本"], error="请求版本与当前知识库不同")
        if name == "End":
            return self.result(data={"answer": query, "finished": True})
        if not query and name!='Function_Locator':return self.result('needs_input',missing=['非空问题或查询对象'])
        corpus=params.get('corpus','synthetic')
        if name=='Hybrid_Search' and (not isinstance(corpus,str) or corpus not in {'synthetic','research'}):
            return self.result('needs_input',missing=['合法语料类型：synthetic或research'])
        if params.get('function_id') is not None and (not isinstance(params['function_id'],str) or not re.fullmatch(r'FUNC-\d{3}',params['function_id'])):
            return self.result('needs_input',missing=['合法的单个功能编号'])
        outside=unsupported_equipment_target(query)
        if outside and name in {'Hybrid_Search','Function_Locator','Fault_Reasoning_Engine'}:
            return self.result('not_covered',coverage={'equipment_scope':f'当前合成ICP资料没有{outside}设备的诊断模型，不能把其故障强行对应为ICP功能。请说明要查询的ICP模块、功能或接口现象。'})
        if name=='Hybrid_Search' and asks_equipment_requirements(query):
            return self.result('not_covered',coverage={'SystemRequirement':'原始资料未提供正式设备系统需求条目。项目说明与非目标设备示例均不能当作SYN-ICP-9000的正式设备需求。'})
        if name in {'Fault_History_Query','Hybrid_Search'} and asks_history_proof(query):
            return self.result(coverage={'view':'policy','explanation':HISTORY_PROOF_POLICY,
                'scope':'系统证据使用边界，未认定本次故障原因'})
        catalog=catalog_kind(query) if not (name=='Hybrid_Search' and corpus=='research') else None
        if catalog and (name==TOOLS.get(catalog,'Hybrid_Search') or name=='Hybrid_Search'):
            return self.catalog(query,catalog,params)
        if name == "Function_Locator":
            if not query:
                return self.result("needs_input", missing=["故障现象或功能描述"])
            if os.getenv("RAGFLOW_API_KEY"):
                ids=[x.strip() for x in os.getenv("RAGFLOW_ENTITY_DATASET_IDS","").split(",") if x.strip()]
                if not ids: return self.result("error",error="RAGFlow功能实体数据集尚未配置")
                try:
                    documents=[x.strip() for x in os.getenv("RAGFLOW_FUNCTION_DOCUMENT_IDS","").split(",") if x.strip()]
                    # The entity type expands sparse name matches within the scoped
                    # function document. Keep semantic ranking and user confirmation;
                    # never fill missing candidates with fabricated or local results.
                    value=await ragflow_search("子功能 "+query,ids,30,similarity_threshold=0.0,document_ids=documents,vector_similarity_weight=.8)
                    found=[];seen=set()
                    for chunk in value.get("chunks",[]):
                        content=chunk.get("content","")
                        if f"[CORPUS_VERSION={self.kb.version}]" not in content: continue
                        match=re.search(r"\[RECORD_ID=(FUNC-\d+)\]",content)
                        record=self.kb.records.get(match[1]) if match else None
                        if record and record["kind"]=="function" and record["id"] not in seen:
                            seen.add(record["id"]);found.append({**record,"score":chunk.get("similarity",0)})
                    leaves=[r for r in found if r["fields"]["parent_id"]]
                    found=(leaves or found)[:3]
                    return self.result("ok" if found else "not_found",records=found,coverage={"retrieval":"ragflow_entity_hybrid"})
                except IntegrationError as exc:
                    return self.result("error",error=str(exc))
            found = self.kb.locate(query)
            return self.result("ok" if found else "not_found", records=found,coverage={"retrieval":"local_bm25_baseline"})
        if name == "Fault_Reasoning_Engine":
            fid = params.get("confirmed_function_id")
            if not fid:
                located=await self.execute("Function_Locator",{"query":query})
                if located["status"]!="ok": return located
                return {**located,"status":"needs_confirmation","missing_fields":["用户确认的功能"]}
            # Only the session controller may attest confirmation. Direct callers cannot forge it.
            if not params.get("_confirmation_validated"):
                return self.result("needs_confirmation", missing=["绑定诊断任务的有效功能确认"])
            function = self.kb.records.get(fid)
            if not function or function["kind"] != "function":
                return self.result("not_found")
            calls = [("FTA_Query", {"query": fid, "function_id": fid}),
                     ("FMECA_Query", {"query": query, "function_id": fid}),
                     ("RBD_Query", {"query": fid, "function_id": fid}),
                     ("Fault_History_Query", {"query": query, "function_id": fid})]
            runner = self.branch_runner or self.execute
            async def branch(name, arguments):
                for attempt in range(2):
                    try:
                        value = await runner(name, arguments)
                    except IntegrationError:
                        # Provider bodies can contain private request data. Preserve
                        # the branch name and outcome, never the raw exception.
                        value = self.result("error", error="分支服务执行失败")
                    if value.get("status") != "error" or attempt == 1:
                        if attempt:
                            value = {**value, "coverage": {**value.get("coverage", {}), "workflow_retries": attempt}}
                        return value
                    await asyncio.sleep(.2)
            values = await asyncio.gather(*(branch(n, p) for n, p in calls))
            if any(v.get("corpus_version")!=self.kb.version for v in values):
                return self.result("error",error="多源分支返回了不同知识库版本，已停止合并")
            ev = {e["id"]: e for v in values for e in v["evidence"]}
            failed = [n for (n, _), v in zip(calls, values) if v["status"] == "error"]
            return {**self.result("error" if failed else "ok", data={"function": function, "branches": dict(zip([n for n,p in calls], values))},
                                 error="以下诊断分支重试后仍未完成："+"、".join(failed) if failed else None),
                    "evidence": list(ev.values()), "coverage": {n: v["status"] for (n,p),v in zip(calls,values)}}
        if name == "HMC_Query":
            complete=list(dict.fromkeys(code.replace('－','-') for code in re.findall(r'(?<!\d)\d{8}[-－](?:\*\*|\d{2})[-－]\d{4}(?!\d)',query)))
            if len(complete)>1:return self.result('needs_input',missing=['本轮查询的单个HMC编号；可逐个查询后汇总，不能只取第一个冒充全部结果'])
            # A clarification may contain the original partial code followed by
            # the completed code. Prefer the unique complete identifier.
            parts = re.search(r"(\d{8})(?:[-－](\*\*|\d{1,2}))?(?:[-－](\d{1,4}))?", complete[0] if complete else query)
            all_codes = self.kb.by_kind["hmc"]
            if parts:
                lcn, hamc, hsc = parts.groups()
                exact = [r for r in all_codes if r["id"] == "-".join([lcn, hamc or "", hsc or ""])]
                if exact:
                    return self.result(records=exact, coverage={"match": "exact"})
                if hamc and hsc and len(hamc)==2 and len(hsc)==4:
                    wildcard = [r for r in all_codes if r["fields"]["lcn"]==lcn and r["fields"]["hsc"]==hsc and r["fields"]["hamc"]=="**"]
                    return self.result("ok" if wildcard else "not_found", records=wildcard, coverage={"match": "same_lcn_hsc_hamc_wildcard"})
                candidates = [r for r in all_codes if r["fields"]["lcn"]==lcn and
                              (not hamc or r["fields"]["hamc"].startswith(hamc)) and
                              (not hsc or r["fields"]["hsc"].startswith(hsc))]
                missing = [x for x,v,length in [("HAMC",hamc,2),("HSC",hsc,4)] if not v or len(v)!=length]
                return self.result("needs_input" if candidates else "not_found", records=candidates[:12], missing=missing)
            found = self.kb.search(query, ["hmc"], 6)
            return self.result("ok" if found else "needs_input", records=found, missing=[] if found else ["HMC代码或现象"])
        if name == "MFL_PFL_Query":
            if re.search(r"(?<![A-Za-z])PFL(?![A-Za-z])|飞行员维护", query, re.I):
                return self.result("not_covered", coverage={"PFL": "原始资料未提供PFL"})
            return self.lookup(query, "mfl")
        if name in {"FTA_Query", "RBD_Query"}:
            identifiers=set(re.findall(r'(?:FTA|RBD)-SYN-\d{3}',query.upper()))
            if len(identifiers)>1:return self.result('needs_input',missing=['本轮查询的单个工程模型；多个模型应逐个核对'])
            kind = "fta" if name == "FTA_Query" else "rbd"
            rid = re.search(rf"{kind.upper()}-SYN-\d{{3}}", query.upper())
            fid = params.get("function_id") or next(iter(re.findall(r"FUNC-\d+", query.upper())), None)
            if rid:
                found = [self.kb.records[rid[0]]] if rid[0] in self.kb.records else []
            elif fid:
                found = self.kb.model_for_function(fid, kind)
            else:
                found = self.kb.search(query, [kind], 3)
            if not found:
                return self.result("not_covered" if fid else "not_found", coverage={kind: "当前资料未找到对应模型"})
            calculating = kind == "fta" or bool(re.search(r"计算|可靠度|可靠性是多少|\d+\s*(?:小时|h\b)", query, re.I)) or "hours" in params
            if not calculating:
                return self.result(records=found)
            if kind == "rbd":
                try:
                    hours = mission_hours(query, params.get("hours"))
                except (ValueError,TypeError,OverflowError):
                    return self.result("needs_input", records=found, missing=["单个合法的非负有限任务时间（小时、分钟或天）"])
                if hours is None:
                    return self.result("needs_input", records=found, missing=["任务时间（小时）"])
                if len(found)>1 and not rid and not fid:
                    named=[r for r in found if r['name'].removesuffix('可靠性模型') in query]
                    if len(named)!=1:
                        return self.result("needs_input", records=found, missing=["明确的RBD模型编号或系统功能"])
                    found=named
                try:
                    results = [{"model": r, "hours": float(hours), "reliability": reliability(r["fields"]["expression"], r["fields"]["rates"], float(hours)), "unit": "h", "formula": "R(t)=exp(-λt)；串联相乘，并联1-各失效概率之积"} for r in found]
                except (ValueError, TypeError, OverflowError):
                    return self.result("needs_input", records=found, missing=["合法的非负有限任务时间（小时）"])
                return self.result(data=results, records=found)
            results = []
            for r in found:
                fields = r["fields"]
                probability = fault_tree_probability({e["id"]:e["probability"] for e in fields["events"]}, fields["minimal_cuts"])
                results.append({"model": r, "probability": probability, "minimal_cuts": minimal_cuts(fields["minimal_cuts"]),
                                "assumption": fields["assumption"], "method": "唯一底事件状态枚举；重叠割集不独立相加"})
            return self.result(data=results, records=found)
        if name in {"FMECA_Query", "Fault_History_Query"}:
            kind = "fmeca" if name == "FMECA_Query" else "history"
            fid = params.get("function_id") or next(iter(re.findall(r'FUNC-\d+',query.upper())),None)
            if fid:
                pool = [r for r in self.kb.by_kind[kind] if fid in json.dumps(r["fields"], ensure_ascii=False)]
                ranked = self.kb.search(query, [kind], 100)
                pool_ids = {r["id"] for r in pool}
                found = [r for r in ranked if r["id"] in pool_ids][:6] or pool[:6]
                return self.result("ok" if found else "not_covered", records=found)
            return self.lookup(query, kind)
        # Hybrid_Search: use real RAGFlow when configured. Baseline is labelled explicitly.
        comparison=[kind for kind in topics(query) if kind in TOOLS]
        if name=='Hybrid_Search' and corpus!='research' and len(comparison)>1 and re.search(r'区别|不同|比较|对比|关系',query):
            found=[r for kind in comparison for r in self.kb.by_kind[kind][:2]]
            return self.result(records=found,coverage={'view':'comparison','topics':[LABELS[k] for k in comparison]})
        if os.getenv("RAGFLOW_API_KEY"):
            try:
                corpus=params.get("corpus","synthetic")
                variable="RAGFLOW_RESEARCH_DATASET_IDS" if corpus=="research" else "RAGFLOW_SYNTHETIC_DATASET_IDS"
                ids = [x.strip() for x in (os.getenv(variable) or os.getenv("RAGFLOW_DATASET_IDS", "")).split(",") if x.strip()]
                result = await ragflow_search(query, ids,12)
                found=[]; source_ids=[]
                for chunk in result.get("chunks",[]):
                    content=chunk.get("content", "")
                    if f"[CORPUS_VERSION={self.kb.version}]" not in content:
                        continue
                    evidence_ids=re.findall(r"\[SOURCE_ID=([^\]]+)\]",content)
                    evidence_ids=[eid for eid in evidence_ids if eid in self.kb.data["evidence"] and self.kb.data["evidence"][eid]["corpus"]==corpus]
                    if evidence_ids and all(navigation_only(self.kb.data['evidence'][eid]['raw_text']) for eid in evidence_ids):continue
                    if evidence_ids:
                        found.append(chunk);source_ids.extend(evidence_ids)
                    if len(found)>=6:break
                term=definition_term(query)
                if corpus=='synthetic':
                    modules,support=module_subject_records(self.kb,term)
                    if modules:
                        found=modules+found
                        source_ids=[eid for r in modules+support for eid in r['evidence_ids']]+source_ids
                evidence = self.kb.evidences(source_ids)
                if term and evidence and not term_in_sources(term,evidence):
                    return {**self.result('not_covered',data=found,coverage={'view':'insufficient_evidence',
                        'explanation':'本次检索未找到足以直接解释“'+term+'”的材料，暂不将相关段落拼成定义。请补充术语、指定文档，或在右侧核对候选资料。',
                        'retrieval':'ragflow_hybrid','candidate_count':len(found)}),'evidence':evidence}
                return {**self.result("ok" if found else "not_found", data=found, coverage={"retrieval": "ragflow_hybrid"}), "evidence": evidence}
            except IntegrationError as exc:
                return self.result("error", error=str(exc))
        corpus = params.get("corpus", "synthetic")
        if corpus not in {"synthetic", "research"}:
            return self.result("needs_input", missing=["合法语料类型：synthetic或research"])
        found = self.kb.search(query, limit=6, corpus=corpus)
        return self.result("ok" if found else "not_found", records=found, coverage={"retrieval": "local_bm25_baseline", "vector": "RAGFlow未配置；当前未进行向量检索"})

    def lookup(self, query: str, kind: str):
        ids = re.findall(r"(?:FM(?:ECA)?|MFL|HIS)-SYN-\d{3}", query.upper())
        ids = [i.replace("FMECA-SYN-", "FM-SYN-") for i in ids]
        if ids:
            prefixes={'fmeca':'FM','mfl':'MFL','history':'HIS'}
            direct=[i for i in ids if i.startswith(prefixes[kind]+'-')]
            if direct:
                found = [self.kb.records[i] for i in direct if i in self.kb.records and self.kb.records[i]["kind"]==kind]
            else:
                sources=[self.kb.records[i] for i in ids if i in self.kb.records]
                modes={i for r in sources for i in r['fields'].get('failure_mode_ids',[])}
                modes.update(r['id'] for r in sources if r['kind']=='fmeca')
                found=[r for r in self.kb.by_kind[kind] if r['id'] in modes or modes.intersection(r['fields'].get('failure_mode_ids',[]))]
                return self.result('ok' if found else 'not_found',data=found,records=found+sources,
                                   coverage={'lookup':'explicit_source_relationship','source_record_ids':[r['id'] for r in sources]})
        else:
            found = self.kb.search(query, [kind], 6)
        return self.result("ok" if found else "not_found", records=found)

    def catalog(self,query,kind,params):
        pool=self.kb.by_kind.get(kind,[])
        all_count=len(pool)
        pool,filters=scope_records(self.kb,query,kind,pool)
        groups=[]
        if kind in {'hmc','mfl','fmeca','history'}:
            for alias,_ in DOMAINS:
                items=[r for r in pool if record_domain(self.kb,r)==alias]
                if items:groups.append({'name':self.kb.records['SUB-'+alias]['name'],'count':len(items),'example':items[0]['id']})
        limit=100 if re.search(r'全部|所有|完整列',query) else 12
        requested=re.search(r'前\s*(\d+)\s*(?:条|个|项)',query)
        if requested:limit=max(1,min(100,int(requested[1])))
        representatives=[next(r for r in pool if r['id']==g['example']) for g in groups]
        ordered=list({r['id']:r for r in representatives+pool}.values())
        shown=ordered[:limit]
        return self.result('ok' if shown else 'not_found',data=shown,records=shown+pool,coverage={
            'view':'catalog','kind':kind,'label':LABELS[kind],'total':all_count,'matched':len(pool),'returned':len(shown),'groups':groups,'filters':filters,
            'encoding':{'segments':['LCN','HAMC','HSC'],'wildcard':'**为原手册合法HAMC通配段'} if kind=='hmc' else None})

