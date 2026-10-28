"""Source-grounded typed attributes; model candidates are audited separately."""
import copy
import hashlib
from pathlib import Path
import re

COMMON=['id','name','description','source','note','probability','severity','occurrence','detection_level']
SCHEMAS={
 'SystemRequirement':['req_id','name','statement','req_type','verify_criteria','priority'],
 'TopLevelFunction':['func_id','name','description','viewpoint'],
 'SubFunction':['sub_func_id','name','description','input','output','trigger'],
 'RBD_Model':['id','name','description','source','note','mission_time'],
 'RBD_SystemSuccess':['id','name','description','system_reliability','system_mtbf'],
 'RBD_Block':['id','name','description','failure_rate','reliability','mtbf','is_redundant'],
 'RBD_LogicGate':['id','name','description','gate_kind','k','n'],
 **{t:COMMON for t in ['FTATopEvent','FTAIntermediateEvent','FTABasicEvent','FTAGate','FailureMode','FailureCause','FailureEffect','DetectionMethod','MitigationMeasure']}}


def profile_digest():return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def merge_node(existing,incoming):
    """Merge cross-document views without letting a reference stub erase engineering values."""
    if existing is None:return copy.deepcopy(incoming)
    result=copy.deepcopy(existing);old=result['properties'];new=incoming['properties']
    for key in ['failure_rate','probability','unit','gate_kind']:
        if old.get(key) is not None and new.get(key) is not None and old[key]!=new[key]:
            raise ValueError('Conflicting source engineering attributes for '+incoming['id'])
    richer=sum(v is not None for v in new.values())>sum(v is not None for v in old.values())
    for key,value in new.items():
        if value is not None and (old.get(key) is None or richer):old[key]=copy.deepcopy(value)
    result['evidence_ids']=list(dict.fromkeys(existing['evidence_ids']+incoming['evidence_ids']))
    old['source_ids']=list(dict.fromkeys(existing['properties'].get('source_ids',[])+new.get('source_ids',[])))
    unknown=set(existing['properties'].get('unknown_fields',[])+new.get('unknown_fields',[]))
    old['unknown_fields']=sorted(k for k in unknown if old.get(k) is None)
    if richer:result.update(label=incoming['label'],type=incoming['type'])
    return result


def typed_graph(kb,projection):
    graph=copy.deepcopy(projection);nodes={n['id']:n for n in graph['nodes']};outgoing={nid:[] for nid in nodes}
    for e in graph['edges']:outgoing[e['source']].append(e['target'])
    for node in graph['nodes']:
        nid=node['id'];kind=node['type'];props=node['properties'];required=SCHEMAS.get(kind,[])
        record=kb.records.get(nid)
        mode_id=re.match(r'FM-SYN-\d{3}',nid)
        mode=kb.records.get(mode_id[0]) if mode_id else None
        rbd=kb.records.get(graph['id']) if graph['type']=='rbd' else None
        props.update(id=nid,name=node['label'])
        props.setdefault('description',record['fields'].get('description',node['label']) if record else node['label'])
        props['source_ids']=list(node['evidence_ids'])
        props['source']='；'.join(kb.data['evidence'][eid]['document']+' / '+str(kb.data['evidence'][eid].get('section') or f"表{kb.data['evidence'][eid].get('table')}行{kb.data['evidence'][eid].get('row')}") for eid in node['evidence_ids'])
        props.setdefault('note','仅使用来源明确的属性；缺值不猜填。')
        derived=[]
        if mode:
            f=mode['fields']
            if kind=='FailureMode':props.update(severity=f['severity'],occurrence=f['probability_class'])
            else:
                # FMECA grades describe the failure mode, not the detector,
                # cause or mitigation as independent engineering objects.
                props.update(related_failure_mode_id=mode['id'],related_failure_mode_severity=f['severity'],
                    related_failure_mode_occurrence=f['probability_class'])
                props['severity']=None;props['occurrence']=None
            if kind=='FailureEffect':props['effect_level']='local' if props.get('field')=='local_effect' else 'final'
        if kind=='FTABasicEvent':
            props['probability_unit']='dimensionless'
            linked=kb.records.get(props.get('failure_mode_id'))
            if linked:
                props.update(severity=linked['fields']['severity'],occurrence=linked['fields']['probability_class'])
                props['source_ids']=list(dict.fromkeys(props['source_ids']+linked['evidence_ids']))
        if kind=='FTATopEvent' and record:props['probability']=record['fields']['source_probability'];props['probability_unit']='dimensionless'
        if kind=='RBD_Model' and record:props['description']=record['fields']['criterion'];props['mission_time_unit']='h'
        if kind=='RBD_SystemSuccess' and rbd:
            props['description']=rbd['fields']['criterion']
            props['reliability_samples']=[{'hours':float(row[0]),'reliability':row[1]} for row in rbd['fields']['expected'][1:]]
        if kind=='RBD_Block':
            seen=set();pending=list(outgoing[nid]);redundant=False
            while pending:
                parent=pending.pop()
                if parent in seen:continue
                seen.add(parent)
                if nodes[parent]['properties'].get('gate_kind')=='parallel':redundant=True
                pending.extend(outgoing[parent])
            props['is_redundant']=redundant;derived.append('is_redundant')
        if kind=='RBD_LogicGate':props['description']='串联成功逻辑' if props['gate_kind']=='series' else '并联成功逻辑'
        for key in required:props.setdefault(key,None)
        props['unknown_fields']=[key for key in required if props[key] is None]
        props['derived_fields']=derived
        props['attribute_origins']={'related_failure_mode_severity':'linked source failure-mode grade; not an entity grade',
            'related_failure_mode_occurrence':'linked source failure-mode occurrence class; not numerical probability'} if mode and kind!='FailureMode' else {}
        props['attribute_policy']='source_enrichment; not raw model-extracted properties'
        props['unknown_reason']='原文未给出，或该字段需要指定任务时间/不适用于此门类型；不能把检测方法当作检测等级。'
    # Schema inverses and causal shortcuts are derived only from source-backed
    # edges. Keep the derivation visible instead of attributing them to the LLM.
    existing={(e['source'],e['target'],e['type']) for e in graph['edges']}
    def derived(a,b,kind,evidence,rule):
        key=(a,b,kind)
        if key not in existing:
            graph['edges'].append({'source':a,'target':b,'type':kind,
                'evidence_ids':list(dict.fromkeys(evidence)),'derivation':rule})
            existing.add(key)
    source_edges=list(graph['edges'])
    for edge in source_edges:
        if edge['type']=='has_subfunction':
            derived(edge['target'],edge['source'],'is_subfunction_of',edge['evidence_ids'],'inverse of source has_subfunction')
        if edge['type']=='gate_sub_input':
            derived(edge['source'],edge['target'],'gate_produce_output',edge['evidence_ids'],'nested RBD gate output is its parent gate input')
        if edge['type']=='gate_input':
            for output in source_edges:
                if output['source']==edge['target'] and output['type']=='gate_output':
                    derived(edge['source'],output['target'],'lead_to_event',edge['evidence_ids']+output['evidence_ids'],
                        'FTA event-to-gate-to-event path; not an independent probability assumption')
    graph['graph_profile_sha256']=profile_digest()
    graph['attribute_origin']='source_grounded_normalization'
    return graph
