"""Reconcile native model candidates with source gold, without inventing missed nodes."""
import json
from collections import Counter,defaultdict
from pathlib import Path
import re
import sys
import unicodedata

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'));sys.path.insert(0,str(ROOT))
from icp.knowledge import Knowledge
from icp.graphs import graph_for
from icp.graph_schema import typed_graph,SCHEMAS,profile_digest
from scripts.scoped_graph_sources import source_units
from scripts.scoped_graph_provenance import export_manifest


def canonical(value):
    return unicodedata.normalize('NFKC',str(value)).translate(str.maketrans({'‑':'-','–':'-','—':'-'})).strip().casefold()


def reconcile(expected,raw):
    nodes={n['id']:n for n in expected['nodes']};aliases=defaultdict(set)
    for nid,n in nodes.items():
        for text in [nid,n['label']]:aliases[canonical(text)].add(nid)
        if n['type']=='RBD_Block':aliases[canonical(nid.split('-BLOCK-')[-1])].add(nid)
    entities=[e for t in raw.get('templates',[]) for e in t.get('entities',[])]
    relations=[e for t in raw.get('templates',[]) for e in t.get('relations',[])]
    mapping={};matched=defaultdict(list);unresolved=[]
    def resolve(name):
        exact=aliases.get(canonical(name),set())
        if len(exact)==1:return next(iter(exact))
        # Mixed ID/display names can be reconciled only when the explicit ID
        # has an unambiguous boundary, never by fuzzy semantic similarity.
        text=canonical(name)
        hits=[nid for nid in nodes if re.search(r'(?<![a-z0-9-])'+re.escape(canonical(nid))+r'(?![a-z0-9-])',text)]
        return hits[0] if len(hits)==1 else None
    for entity in entities:
        nid=resolve(entity.get('name',''))
        if nid:mapping[entity['name']]=nid;matched[nid].append(entity)
        else:unresolved.append({'name':entity.get('name'),'type':entity.get('type')})
    wrong_types=[{'id':nid,'expected':nodes[nid]['type'],'actual':e.get('type')} for nid,values in matched.items() for e in values if e.get('type')!=nodes[nid]['type']]
    missing=sorted(set(nodes)-set(matched));duplicates={nid:len(values) for nid,values in matched.items() if len(values)>1}
    expected_edges={(e['source'],e['target'],e['type']) for e in expected['edges']}
    edge_hits=set();bad_edges=[]
    for edge in relations:
        source=mapping.get(edge.get('from')) or resolve(edge.get('from',''))
        target=mapping.get(edge.get('to')) or resolve(edge.get('to',''))
        key=(source,target,edge.get('type'))
        if key in expected_edges:edge_hits.add(key)
        else:bad_edges.append({'from':edge.get('from'),'to':edge.get('to'),'type':edge.get('type'),'reason':'not a source-supported directed relation'})
    raw_property_checks=[]
    for nid,values in matched.items():
        required=SCHEMAS.get(nodes[nid]['type'],[])
        properties=values[0].get('properties',{})
        raw_property_checks.append({'id':nid,'required':required,'missing':[k for k in required if k not in properties],
            'note':'description prose is not a typed properties object'})
    strict=not (missing or duplicates or wrong_types or unresolved or bad_edges or expected_edges-edge_hits or any(x['missing'] for x in raw_property_checks))
    return {'expected_entities':len(nodes),'matched_entities':len(matched),'correct_type_entities':sum(any(e.get('type')==nodes[nid]['type'] for e in values) for nid,values in matched.items()),
        'missing_entities':missing,'duplicate_entities':duplicates,'type_mismatches':wrong_types,'unresolved_entities':unresolved,
        'expected_relations':len(expected_edges),'matched_relations':len(edge_hits),
        'missing_relations':[list(e) for e in sorted(expected_edges-edge_hits)],'unsupported_relations':bad_edges,
        'raw_typed_properties':raw_property_checks,'raw_strict_passed':strict,
        'candidate_normalization':{'nodes':[{**nodes[nid],'candidate_names':[e['name'] for e in values],
            'attribute_origin':'source enrichment after exact candidate identity match; not raw LLM extraction'} for nid,values in matched.items()],
            'edges':[e for e in expected['edges'] if (e['source'],e['target'],e['type']) in edge_hits],
            'policy':'missing model candidates and relations are NOT filled by the deterministic engineering projection'}}


def main():
    kb=Knowledge();units=[];normalized=[];provenance=export_manifest()
    for group,rid,packet in source_units(kb):
        raw_path=ROOT/'outputs/scoped-graphs'/(rid+'.json')
        if not raw_path.exists():units.append({'record_id':rid,'group':group,'status':'not_exported'});continue
        origin=provenance.get('units',{}).get(rid,{})
        import hashlib
        if (not origin.get('current_export_verified') or provenance.get('corpus_version')!=kb.version
                or origin.get('export_sha256')!=hashlib.sha256(raw_path.read_bytes()).hexdigest()):
            units.append({'record_id':rid,'group':group,'status':'current_export_not_verified'});continue
        projection=graph_for(kb,group,'all' if group=='function' else rid)
        if group=='function':
            ids={r['id'] for r in packet['source_records']}
            projection['nodes']=[n for n in projection['nodes'] if n['id'] in ids]
            projection['edges']=[e for e in projection['edges'] if e['source'] in ids and e['target'] in ids]
        expected=typed_graph(kb,projection)
        row=reconcile(expected,json.loads(raw_path.read_text(encoding='utf-8')))
        normalized.append({'record_id':rid,'group':group,**row.pop('candidate_normalization')})
        units.append({'record_id':rid,'group':group,'status':'audited','provenance':origin,**row})
    summary={}
    for group in ['function','rbd','fta','fmeca']:
        rows=[r for r in units if r['group']==group];done=[r for r in rows if r['status']=='audited']
        summary[group]={'expected_units':len(rows),'audited_units':len(done),**{k:sum(r[k] for r in done) for k in ['expected_entities','matched_entities','correct_type_entities','expected_relations','matched_relations']}}
    report={'date':'2026-10-24','corpus_version':kb.version,'graph_profile_sha256':profile_digest(),'summary':summary,'units':units,
        'complete':all(r['status']=='audited' for r in units),'raw_strict_passed':all(r.get('raw_strict_passed',False) for r in units),
        'separation':'native outputs, identity-matched normalized candidates, and deterministic engineering projection are separately reported',
        'material_gaps':['PFL not provided','formal equipment SystemRequirement not provided']}
    (ROOT/'outputs/scoped-graph-audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    (ROOT/'outputs/scoped-graphs-normalized.json').write_text(json.dumps({'corpus_version':kb.version,'units':normalized},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'complete':report['complete'],'raw_strict_passed':report['raw_strict_passed'],'summary':summary}))


if __name__=='__main__':main()
