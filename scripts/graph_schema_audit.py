"""Audit source table fields and every normalized schema field, with explicit unknowns."""
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from icp.knowledge import Knowledge
from icp.ingest import docx
from icp.graphs import graph_for
from icp.graph_schema import typed_graph,SCHEMAS,profile_digest


def main():
    kb=Knowledge();checks=[];fields=[];documents={d['id']:d for d in kb.data['documents']}
    def source(sid):
        document=documents[sid];blob=(ROOT/document['path']).read_bytes()
        if hashlib.sha256(blob).hexdigest()!=document['sha256']:raise RuntimeError('Original source digest changed')
        return docx(blob)
    def check(entity,field,actual,expected,location):
        checks.append({'entity':entity,'field':field,'source':location,'passed':actual==expected,'actual':actual,'expected':expected})
    function=typed_graph(kb,graph_for(kb,'function','all'));function_nodes={n['id']:n for n in function['nodes']}
    for row_number,row in enumerate(source('DOC-004')['tables'][3][1:],2):
        nid,parent,name,description,module=row
        node=function_nodes[nid];location=f'DOC-004 table4 row{row_number}'
        check(nid,'name',node['label'],name,location);check(nid,'description',node['properties']['description'],description,location)
        expected_type='TopLevelFunction' if parent=='—' else 'SubFunction'
        check(nid,'entity_type',node['type'],expected_type,location)
        if parent!='—':
            check(nid,'parent_direction',any(e['source']==parent and e['target']==nid and e['type']=='has_subfunction' for e in function['edges']),True,location)
            check(nid,'inverse_parent_direction',any(e['source']==nid and e['target']==parent and e['type']=='is_subfunction_of' for e in function['edges']),True,location)
    fmeca=typed_graph(kb,graph_for(kb,'fmeca','all'));fm_nodes={n['id']:n for n in fmeca['nodes']}
    source_columns=['ordinal','id','product_function','function_name','mode','cause','stage','local_effect','final_effect','severity','probability_class','detection','improvement','mitigation']
    for table_number,table in enumerate(source('DOC-006')['tables'][3:9],4):
        for row_number,row in enumerate(table[1:],2):
            original=dict(zip(source_columns,row));nid=original['id'];location=f'DOC-006 table{table_number} row{row_number}'
            for key,value in original.items():check(nid,'record.'+key,kb.records[nid]['fields'].get(key),value,location)
            for key in ['mode','cause','stage','local_effect','final_effect','severity','probability_class','detection','mitigation']:
                check(nid,'graph.'+key,fm_nodes[nid]['properties'][key],original[key],location)
            for key in ['cause','local_effect','final_effect','detection','mitigation']:
                node=fm_nodes[nid+'-'+key]
                check(node['id'],'name',node['label'],original[key],location)
                check(node['id'],'related_failure_mode_severity',node['properties']['related_failure_mode_severity'],original['severity'],location)
                check(node['id'],'related_failure_mode_occurrence',node['properties']['related_failure_mode_occurrence'],original['probability_class'],location)
                check(node['id'],'no_independent_entity_grade',(node['properties']['severity'],node['properties']['occurrence']),(None,None),location)
    rbd_tables=source('DOC-008')['tables']
    for index,model in enumerate(kb.by_kind['rbd']):
        rid=model['id'];graph=typed_graph(kb,graph_for(kb,'rbd',rid));nodes={n['id']:n for n in graph['nodes']}
        table_number=3+index*2
        for row_number,row in enumerate(rbd_tables[table_number-1][1:],2):
            node=nodes[rid+'-BLOCK-'+row[1]];location=f'DOC-008 table{table_number} row{row_number}'
            check(node['id'],'failure_rate',node['properties']['failure_rate'],float(row[3]),location)
            check(node['id'],'unit',node['properties']['unit'],'1/h',location)
        expected=[{'hours':float(row[0]),'reliability':row[1]} for row in rbd_tables[table_number][1:]]
        check(rid+'-SUCCESS','reliability_samples',nodes[rid+'-SUCCESS']['properties']['reliability_samples'],expected,f'DOC-008 table{table_number+1}')
    fta_tables=source('DOC-007')['tables']
    for index,model in enumerate(kb.by_kind['fta']):
        rid=model['id'];nodes={n['id']:n for n in typed_graph(kb,graph_for(kb,'fta',rid))['nodes']}
        table_number=4+index*3
        for row_number,row in enumerate(fta_tables[table_number-1][1:],2):
            nid=row[0];properties=nodes[nid]['properties'];location=f'DOC-007 table{table_number} row{row_number}'
            for key,expected in [('failure_mode_id',row[1]),('module',row[2]),('name',row[2]+':'+row[3]),('cause',row[4]),('probability',float(row[5])),('probability_unit','dimensionless')]:
                check(nid,key,properties.get(key),expected,location)
    for projection in kb.data['graphs']:
        graph=typed_graph(kb,projection);identities=[n['id'] for n in graph['nodes']];edges=graph['edges']
        check(graph['id'],'duplicate_nodes',len(identities),len(set(identities)),'graph identity invariant')
        keys=[(e['source'],e['target'],e['type']) for e in edges]
        check(graph['id'],'duplicate_directed_relations',len(keys),len(set(keys)),'directed relation identity invariant')
        for node in graph['nodes']:
            properties=node['properties']
            for field in SCHEMAS.get(node['type'],[]):
                value=properties.get(field);present=field in properties
                missing=value is None;reason=properties.get('unknown_reason') if missing else None
                passed=present and (not missing or field in properties.get('unknown_fields',[]) and bool(reason))
                if isinstance(value,float):passed=passed and math.isfinite(value)
                fields.append({'graph':graph['id'],'entity':node['id'],'type':node['type'],'field':field,
                    'value':value,'unknown':missing,'reason':reason,'source_ids':properties['source_ids'],
                    'origin':properties['attribute_policy'],'passed':bool(passed)})
    report={'date':'2026-10-24','corpus_version':kb.version,'graph_profile_sha256':profile_digest(),
        'source_checks':checks,'schema_fields':fields,'summary':{'source_checks':len(checks),'source_passed':sum(c['passed'] for c in checks),
            'schema_fields':len(fields),'schema_fields_passed':sum(f['passed'] for f in fields),'unknown_fields':sum(f['unknown'] for f in fields),
            'unique_entity_field_pairs':len({(f['entity'],f['field']) for f in fields}),
            'by_entity_type':dict(Counter(f['type'] for f in fields))},'passed':all(c['passed'] for c in checks) and all(f['passed'] for f in fields),
        'scope':'source table facts and normalized engineering schema; not native LLM extraction accuracy',
        'figure_validation':'原图拓扑采用既有15张来源核对记录，未以本脚本替代图像语义人工核对',
        'unknown_policy':'unknown is permitted only with an explicit reason; unknown is not evidence that the raw schema was fully supplied'}
    (ROOT/'outputs/graph-schema-audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'passed':report['passed'],'summary':report['summary']}))
    return report['passed']


if __name__=='__main__':raise SystemExit(not main())
