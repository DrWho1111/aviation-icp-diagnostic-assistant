"""Source-based graph projection; raw engineering figures remain available unchanged."""
from __future__ import annotations

import hashlib
import re

INTERMEDIATE_NAMES = {
    1: ["双路工作电源均不可用"], 2: ["双通道处理能力同时丧失"],
    3: ["双总线接收路径同时失效", "双总线发送路径同时失效"],
    4: ["主通道失效且备用选择失败"], 5: ["散热能力不足并触发过温"],
}


def graph_for(kb, kind: str, rid: str) -> dict:
    nodes, edges = {}, []
    def node(nid, label, typ, record, properties=None):
        fields=record.get('fields',{})
        props=dict(properties or {})
        if typ in {'TopLevelFunction','SubFunction'}:
            props.update({'name':label,'description':fields.get('description'),
                          'func_id' if typ=='TopLevelFunction' else 'sub_func_id':nid})
            if typ=='TopLevelFunction':props['viewpoint']='SYSTEM_BLACKBOX'
            else:
                for key in ['input','output','trigger']:props[key]=fields.get(key)
                props['unknown_fields']=[k for k in ['input','output','trigger'] if props[k] is None]
        elif typ=='FailureMode':
            props.update({k:fields.get(k) for k in ['mode','cause','stage','severity','probability_class','local_effect','final_effect','detection','mitigation']})
            props['mode_id']=nid
        elif typ=='RBD_Model':
            props.update({k:fields.get(k) for k in ['expression','criterion','assumption','rates_unit','mission_time_unit']})
            props['model_id']=nid
        elif typ=='FTATopEvent':
            props.update({'event_id':nid,'name':label,'assumption':fields.get('assumption')})
        props['source_ids']=record['evidence_ids']
        nodes[nid] = {"id":nid,"label":label,"type":typ,"properties":props,"evidence_ids":record["evidence_ids"]}
        return nid
    def edge(a, b, typ, record):
        edges.append({"source":a,"target":b,"type":typ,"evidence_ids":record["evidence_ids"]})
    images = []
    if kind == "function":
        functions = kb.by_kind["function"]
        architecture=next(d for d in kb.data["documents"] if d["name"]=="01_系统架构与功能说明.docx")
        anchor = {"evidence_ids":[architecture["id"]+"-ARCH-1"]}
        node("SYN-ICP-9000", "SYN-ICP-9000", "System", anchor)
        for r in kb.by_kind["subsystem"]:
            node(r["id"], r["name"], "Subsystem", r)
            edge("SYN-ICP-9000", r["id"], "has_subsystem", anchor)
        subsystems = {r["fields"]["alias"]:r["id"] for r in kb.by_kind["subsystem"]}
        for r in functions:
            node(r["id"], r["name"], "SubFunction" if r["fields"]["parent_id"] else "TopLevelFunction", r)
            parent = r["fields"]["parent_id"]
            edge(parent or "SYN-ICP-9000", r["id"], "has_subfunction" if parent else "has_function", r)
        for r in kb.by_kind["module"]:
            node(r["id"], r["name"], "Module", r)
            edge(subsystems[r["id"].split("-")[1]], r["id"], "contains_module", r)
            edge(r["id"], r["fields"]["function_id"], "implements", r)
        flow={"evidence_ids":[architecture["id"]+"-ARCH-3"]}
        node("EXTERNAL-DATA","外部总线／网络","ExternalInterface",flow)
        node("EXTERNAL-DISPLAY","外部显示设备","ExternalInterface",flow)
        for a,b,label in [("SUB-TCS","SUB-IFC","统一时钟"),("SUB-TCS","SUB-GPP","时钟/温度状态"),
                          ("EXTERNAL-DATA","SUB-IFC","业务数据/时间基准"),("SUB-IFC","SUB-GPP","标准化数据"),
                          ("SUB-GPP","SUB-HMS","故障与运行数据"),("SUB-HMS","SUB-GPP","健康状态"),
                          ("SUB-GPP","SUB-DSP","显示数据/告警"),("SUB-DSP","EXTERNAL-DISPLAY","编码视频/图形")]:
            edge(a,b,"data_flow",flow)
            edges[-1]["label"]=label
        for target in ["SUB-IFC","SUB-GPP","SUB-HMS","SUB-DSP"]:
            edge("SUB-PWR",target,"supplies_power",flow)
        images = next(d["images"] for d in kb.data["documents"] if d["name"]=="01_系统架构与功能说明.docx")
    elif kind == "fta":
        r = kb.records[rid]
        if r["kind"] != "fta":
            raise KeyError(rid)
        f = r["fields"]
        images = [f["image_url"]]
        node(rid, r["name"], "FTATopEvent", r)
        gate = node(rid+"-OR", "OR", "FTAGate", r, {"gate_kind":"OR"})
        edge(gate, rid, "gate_output", r)
        for e in f["events"]:
            node(e["id"], e["module"]+":"+e["name"], "FTABasicEvent", r, e)
        direct = [cut[0] for cut in f["minimal_cuts"] if len(cut)==1]
        groups = [cut for cut in f["minimal_cuts"] if len(cut)>1]
        number = int(rid[-3:])
        if number == 4:
            # Raw image 5-1 includes E04 AND E08 even though E04 already enters OR.
            # Preserve this source branch; absorption is only a calculation operation.
            groups.append(["EVT-SYN-004-BE-04", "EVT-SYN-004-BE-08"])
        for eid in direct:
            edge(eid, gate, "gate_input", r)
        for i, group in enumerate(groups):
            intermediate = node(rid+f"-IE-{i+1}", INTERMEDIATE_NAMES[number][i], "FTAIntermediateEvent", r)
            and_gate = node(rid+f"-AND-{i+1}", "AND", "FTAGate", r, {"gate_kind":"AND"})
            edge(intermediate, gate, "gate_input", r)
            edge(and_gate, intermediate, "gate_output", r)
            for eid in group:
                edge(eid, and_gate, "gate_input", r)
    elif kind == "rbd":
        r = kb.records[rid]
        if r["kind"] != "rbd":
            raise KeyError(rid)
        f = r["fields"]
        images = [f["image_url"]]
        node(rid, r["name"], "RBD_Model", r)
        success = node(rid+"-SUCCESS", f["criterion"], "RBD_SystemSuccess", r)
        edge(rid, success, "model_contains", r)
        tokens = re.findall(r"[A-Z][A-Z0-9-]*|[(),]", f["expression"])
        pos, counter = 0, 0
        def parse():
            nonlocal pos, counter
            value = tokens[pos]
            pos += 1
            if value not in {"SERIES", "PARALLEL"}:
                mid = rid+"-BLOCK-"+value
                node(mid, value, "RBD_Block", r, {"failure_rate":f["rates"][value],"unit":"1/h"})
                return mid
            counter += 1
            gid = node(rid+f"-G{counter}", value, "RBD_LogicGate", r, {"gate_kind":value.lower()})
            pos += 1
            while True:
                child = parse()
                edge(child, gid, "gate_sub_input" if nodes[child]["type"]=="RBD_LogicGate" else "gate_has_input", r)
                if tokens[pos] == ")":
                    pos += 1
                    break
                pos += 1
            return gid
        top = parse()
        edge(top, success, "gate_produce_output", r)
        edge(success, top, "success_driven_by", r)
    elif kind == "fmeca":
        modes = kb.by_kind["fmeca"] if rid == "all" else [kb.records[rid]]
        for r in modes:
            if r["kind"] != "fmeca":
                raise KeyError(rid)
            f = r["fields"]
            mode = node(r["id"], r["name"], "FailureMode", r)
            for key, typ, relation in [("cause","FailureCause","has_failure_cause"),("local_effect","FailureEffect","produce_effect"),
                                      ("final_effect","FailureEffect","produce_effect"),("detection","DetectionMethod","has_detection"),
                                      ("mitigation","MitigationMeasure","has_mitigation")]:
                target = node(r["id"]+"-"+key, f[key], typ, r, {"field":key})
                edge(mode, target, relation, r)
            alias = f["product_function"].split("/")[0].strip()
            module = next((m for m in kb.by_kind["module"] if m["fields"]["alias"]==alias), None)
            if module:
                node(module["id"], module["name"], "Module", module)
                edge(module["id"], mode, "has_failure_mode", r)
    elif kind == "references":
        for r in kb.by_kind["hmc"]+kb.by_kind["mfl"]+kb.by_kind["history"]:
            node(r["id"],r["name"],{"hmc":"HMCRecord","mfl":"MFLRecord","history":"HistoricalCase"}[r["kind"]],r)
            f=r["fields"]
            references=f.get("failure_mode_ids",[])+f.get("function_ids",[])+f.get("hmc_codes",[])
            if r["kind"]=="history":
                references+=re.findall(r"(?:FTA|RBD)-SYN-\d{3}",f["content"])
            for target in dict.fromkeys(references):
                related=kb.records.get(target)
                if related:
                    node(target,related["name"],related["kind"],related)
                    edge(r["id"],target,"references",r)
        for model in kb.by_kind["fta"]:
            for event in model["fields"]["events"]:
                node(event["id"],event["name"],"FTABasicEvent",model)
                related=kb.records[event["failure_mode_id"]]
                node(related["id"],related["name"],"FailureMode",related)
                edge(event["id"],related["id"],"models_failure_mode",model)
    else:
        raise KeyError(kind)
    return {"id":rid,"type":kind,"nodes":list(nodes.values()),"edges":edges,"images":images,
            "corpus_version":kb.version,"synthetic":True,"source_topology":True}


def export_graphs(kb):
    graphs = [graph_for(kb, "function", "all"), graph_for(kb,"fmeca","all"),graph_for(kb,"references","all")]
    for kind in ["fta", "rbd"]:
        graphs.extend(graph_for(kb,kind,r["id"]) for r in kb.by_kind[kind])
    names = {"SystemRequirement":"需求","TopLevelFunction":"顶层功能","SubFunction":"子功能", "RBD_SystemSuccess":"RBD顶层",
             "FTATopEvent":"FTA顶事件","FTABasicEvent":"FTA底事件","FailureMode":"故障模式","FailureEffect":"失效影响"}
    entities = {}
    for graph in graphs:
        for n in graph["nodes"]:
            if n["type"] in names:
                entities[n["id"]] = {"id":"ENTITY-"+n["id"],"entity_id":n["id"],"entity_type":n["type"],
                                     "type_label":names[n["type"]],"text":n["label"],"evidence_ids":n["evidence_ids"],"corpus_version":kb.version}
    return graphs, list(entities.values())
