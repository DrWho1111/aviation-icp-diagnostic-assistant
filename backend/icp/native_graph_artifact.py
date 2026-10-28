"""Load a fully verified native graph artifact, or reject it without backfill."""
import copy
import hashlib
import json
import math
from pathlib import Path
from .graph_schema import SCHEMAS,profile_digest,typed_graph,merge_node

NATIVE_TYPES=set(SCHEMAS)-{'SystemRequirement'}|{'Module'}
NATIVE_RELATIONS={'has_subfunction','is_subfunction_of','model_contains','success_driven_by','gate_has_input',
    'gate_produce_output','gate_sub_input','gate_input','gate_output','lead_to_event','has_failure_mode',
    'has_failure_cause','produce_effect','has_detection','has_mitigation'}


def field_equal(actual,expected,key):
    """Native validation and database readback share the same field semantics."""
    if isinstance(expected,bool) or expected is None:return actual is expected
    if isinstance(expected,(int,float)):
        return isinstance(actual,(int,float)) and not isinstance(actual,bool) and math.isfinite(actual) and math.isclose(actual,expected,rel_tol=1e-12,abs_tol=1e-15)
    if key in {'source_ids','unknown_fields'} and isinstance(expected,list):
        return isinstance(actual,list) and all(isinstance(v,str) for v in [*actual,*expected]) and sorted(actual)==sorted(expected)
    return actual==expected


def artifact_digest(payload):
    body={k:v for k,v in payload.items() if k!='artifact_sha256'}
    return hashlib.sha256(json.dumps(body,ensure_ascii=False,sort_keys=True).encode()).hexdigest()


def source_units(kb):
    return {r['id'] for kind in ['function','fta','rbd','fmeca'] for r in kb.by_kind[kind]
        if kind!='function' or not r['fields']['parent_id']}


def load_release(kb,path):
    path=Path(path)
    if not path.exists():return None
    data=json.loads(path.read_text(encoding='utf-8'))
    if (not data.get('passed') or not data.get('all_source_units_selected')
            or data.get('corpus_version')!=kb.version or data.get('archive_sha256')!=kb.data['archive_sha256']
            or data.get('graph_profile_sha256')!=profile_digest()
            or data.get('artifact_sha256')!=artifact_digest(data)
            or set(data.get('source_unit_ids',[]))!=source_units(kb)):
        raise ValueError('Native graph artifact version/coverage/integrity differs')
    nodes={n['id']:n for n in data['nodes']}
    if len(nodes)!=len(data['nodes']):raise ValueError('Duplicate native entity identity')
    edges={(e['source'],e['target'],e['type']) for e in data['edges']}
    if len(edges)!=len(data['edges']):raise ValueError('Duplicate native directed relation')
    if any(a not in nodes or b not in nodes for a,b,_ in edges):raise ValueError('Native edge endpoint is missing')
    expected=set();expected_edges=set()
    for projection in kb.data['graphs']:
        graph=typed_graph(kb,projection)
        expected.update(n['id'] for n in graph['nodes'] if n['type'] in NATIVE_TYPES)
        expected_edges.update((e['source'],e['target'],e['type']) for e in graph['edges'] if e['type'] in NATIVE_RELATIONS)
    if set(nodes)!=expected or edges!=expected_edges:raise ValueError('Native graph is incomplete or has unsupported objects')
    for node in nodes.values():
        p=node['properties'];required=SCHEMAS.get(node['type'],['id','name','description','source'])
        if any(key not in p for key in required):raise ValueError('Native schema property is absent')
        identities={p[k] for k in ['id','func_id','sub_func_id'] if isinstance(p.get(k),str)}
        if identities!={node['id']}:raise ValueError('Native explicit identity differs')
        if not node.get('evidence_ids') or not set(node['evidence_ids'])<=kb.data['evidence'].keys():raise ValueError('Native source IDs differ')
        if any(p[key] is None and (key not in p.get('unknown_fields',[]) or not p.get('unknown_reason')) for key in required):
            raise ValueError('Native unknown field lacks a reason')
    return {**data,'nodes_by_id':nodes,'edge_keys':edges}


def apply_release(graph,release):
    if release is None:return graph
    result=copy.deepcopy(graph)
    for node in result['nodes']:
        if node['type'] not in NATIVE_TYPES:continue
        native=release['nodes_by_id'].get(node['id'])
        if native is None or native['type']!=node['type']:raise ValueError('Required native entity unavailable')
        markers={'attribute_policy','native_revision','native_artifact_sha256','native_property_fields','source_projection_extra_fields'}
        extra_fields=sorted(set(node['properties'])-set(native['properties'])-markers)
        node['properties'].update(copy.deepcopy(native['properties']))
        node['properties'].update(attribute_policy='verified_native_extraction_after_source_preprocessing',
            native_revision=release['native_revision'],native_artifact_sha256=release['artifact_sha256'],
            native_property_fields=sorted(native['properties']),source_projection_extra_fields=extra_fields)
        node['evidence_ids']=list(dict.fromkeys(node['evidence_ids']+native['evidence_ids']))
    for edge in result['edges']:
        if edge['type'] not in NATIVE_RELATIONS:continue
        if (edge['source'],edge['target'],edge['type']) not in release['edge_keys']:
            raise ValueError('Required native relation unavailable; no engineering backfill')
        edge.update(extraction_origin='verified_ragflow_native',native_revision=release['native_revision'])
    result['native_revision']=release['native_revision']
    return result


def build_publication_graph(kb,release):
    nodes={};edges=[]
    for projection in kb.data['graphs']:
        graph=apply_release(typed_graph(kb,projection),release)
        for node in graph['nodes']:nodes[node['id']]=merge_node(nodes.get(node['id']),node)
        edges.extend(graph['edges'])
    # Cross-document reference stubs can precede the full node. merge_node
    # deliberately avoids overwriting values with null, but a verified native
    # schema must retain its explicit unknown fields, not omit their keys.
    return apply_release({'nodes':list(nodes.values()),'edges':edges},release)
