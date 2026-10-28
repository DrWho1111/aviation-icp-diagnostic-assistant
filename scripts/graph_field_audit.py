"""Check engineering attributes against original tables; audit native candidates separately."""
import json
from pathlib import Path
import sys
from collections import Counter

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from icp.knowledge import Knowledge
from icp.ingest import docx
from icp.graphs import graph_for


def main():
    kb=Knowledge();checks=[]
    def check(name,passed,**detail):checks.append({'name':name,'passed':bool(passed),**detail})
    source=next(d for d in kb.data['documents'] if d['name']=='03_FMECA分析报告.docx')
    tables=docx((ROOT/source['path']).read_bytes())['tables']
    fields=['ordinal','id','product_function','function_name','mode','cause','stage','local_effect','final_effect','severity','probability_class','detection','improvement','mitigation']
    source_modes={row[1]:dict(zip(fields,row)) for t in tables[3:9] for row in t[1:]}
    for rid,expected in source_modes.items():
        actual=kb.records[rid]
        check('original-fmeca-'+rid,all(actual['fields'].get(k)==v for k,v in expected.items()))
    graph=graph_for(kb,'fmeca','all');modes={n['id']:n for n in graph['nodes'] if n['type']=='FailureMode'}
    for rid,original in source_modes.items():
        properties=modes[rid]['properties']
        check('typed-fmeca-'+rid,all(properties.get(k)==original[k] for k in ['mode','cause','stage','severity','probability_class','local_effect','final_effect','detection','mitigation']))
    functions=graph_for(kb,'function','all')
    for n in functions['nodes']:
        if n['type'] not in {'TopLevelFunction','SubFunction'}:continue
        r=kb.records[n['id']];p=n['properties']
        check('function-'+n['id'],p.get('description')==r['fields']['description'] and bool(p['source_ids']))
    for g in kb.data['graphs']:
        ids={n['id'] for n in g['nodes']}
        check('edges-'+g['type']+'-'+g['id'],all(e['source'] in ids and e['target'] in ids and set(e['evidence_ids'])<=set(kb.data['evidence']) for e in g['edges']))
        for n in g['nodes']:
            p=n['properties']
            if n['type']=='RBD_Block':check('rate-'+n['id'],p.get('unit')=='1/h' and p['failure_rate']>=0)
    native=[]
    allowed={
        'function':{'TopLevelFunction','SubFunction','SystemRequirement'},
        'rbd':{'RBD_Model','RBD_SystemSuccess','RBD_Block','RBD_LogicGate'},
        'fta':{'FTATopEvent','FTAIntermediateEvent','FTABasicEvent','FTAGate'},
        'fmeca':{'FailureMode','FailureEffect','FailureCause','DetectionMethod','MitigationMeasure','Module'}}
    expected_names={r['name'] for r in kb.by_kind['function']}
    for group in allowed:
        value=json.loads((ROOT/f'outputs/ragflow-graphs/ICP-{group}.json').read_text(encoding='utf-8'))
        entities=[e for t in value['templates'] for e in t.get('entities',[])];relations=[e for t in value['templates'] for e in t.get('relations',[])]
        problems=[]
        for e in entities:
            if e.get('type') not in allowed[group]:problems.append({'name':e['name'],'reason':'unexpected_type'})
            if group=='function' and e.get('type') in {'TopLevelFunction','SubFunction'} and e['name'] not in expected_names:problems.append({'name':e['name'],'reason':'not_a_source_function'})
            if not isinstance(e.get('properties'),dict):problems.append({'name':e['name'],'reason':'typed_properties_absent'})
        native.append({'group':group,'entities':len(entities),'relations':len(relations),'problems':problems,'strict_passed':not problems,
            'policy':'原生候选原样保留；来源工程图字段通过不能代替原生抽取通过'})
    report={'date':'2026-10-24','corpus_version':kb.version,'source_checks':checks,'source_passed':all(c['passed'] for c in checks),
        'native':native,'native_strict_passed':all(n['strict_passed'] for n in native),
        'unknown_fields':{'SubFunction':['input','output','trigger'],'SystemRequirement':'原包未提供正式设备需求；不生成实体'},
        'scope':'逐字段核对已列出的来源属性；非完整抽取准确率或所有原文schema通过'}
    (ROOT/'outputs/graph-field-audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'source_checks':len(checks),'source_passed':report['source_passed'],'native_strict_passed':report['native_strict_passed']}))


if __name__=='__main__':main()
