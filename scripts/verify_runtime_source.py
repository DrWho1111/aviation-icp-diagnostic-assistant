"""Compare public runtime code/data hashes and native Neo4j readback to source."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from zoneinfo import ZoneInfo

import httpx

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from icp.knowledge import Knowledge
from icp.native_graph_artifact import load_release,NATIVE_TYPES,NATIVE_RELATIONS,field_equal


def main(container,base_url,container_root='/app',native=False,output='runtime-source-verification.json'):
    if not re.fullmatch(r'icp-[a-z0-9-]+',container) or container_root not in {'/app','/icp'}:
        raise ValueError('Choose a named ICP container and an expected application root')
    files=[p for folder in ['backend','frontend/dist'] for p in (ROOT/folder).rglob('*')
           if p.is_file() and '__pycache__' not in p.parts and p.suffix not in {'.pyc','.tsbuildinfo'}]
    files += [ROOT/'data/processed/knowledge.json',ROOT/'data/processed/ocr-reviewed.json',
        ROOT/'scripts/container_start.py',ROOT/'scripts/publish.py']
    artifact=ROOT/'data/processed/native-graph-release.json'
    if native:files.append(artifact)
    expected={p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    code="import sys,json,hashlib;from pathlib import Path;p=json.load(sys.stdin);r=Path(p['root']);print(json.dumps({n:hashlib.sha256((r/n).read_bytes()).hexdigest() if (r/n).is_file() else None for n in p['files']}))"
    docker=r'D:\Apps\Docker\resources\bin\docker.exe'
    if not Path(docker).exists():docker='docker'
    process=subprocess.run([docker,'exec','-i',container,'python','-c',code],
        input=json.dumps({'root':container_root,'files':list(expected)}),capture_output=True,text=True,encoding='utf-8')
    if process.returncode:raise RuntimeError('Runtime hash read failed; no container logs displayed')
    actual=json.loads(process.stdout)
    kb=Knowledge();checks=[]
    def record(name,passed,**fields):checks.append({'name':name,'passed':bool(passed),**fields})
    record('runtime_files_match_current_source',actual==expected,files=len(expected),
        differing_files=[name for name,value in expected.items() if actual.get(name)!=value])
    with httpx.Client(base_url=base_url,trust_env=False,timeout=60) as client:
        health=client.get('/api/health').raise_for_status().json()
        record('runtime_knowledge_version',health.get('corpus_version')==kb.version)
        if native:
            release=load_release(kb,artifact)
            if not release:raise ValueError('Verified native artifact is absent')
            observed={};edges=set();storages=[]
            for kind in ['function','rbd','fta','fmeca']:
                ids=['all'] if kind=='function' else [r['id'] for r in kb.by_kind[kind]]
                for rid in ids:
                    graph=client.get('/api/graphs/'+kind+'/'+rid).raise_for_status().json()
                    storages.append(graph.get('storage'))
                    observed.update({n['id']:n for n in graph['nodes'] if n['type'] in NATIVE_TYPES})
                    edges.update((e['source'],e['target'],e['type'],e.get('native_revision')) for e in graph['edges'] if e['type'] in NATIVE_RELATIONS)
            bad=[]
            for nid,node in release['nodes_by_id'].items():
                candidate=observed.get(nid,{});p=candidate.get('properties',{})
                if (candidate.get('type')!=node['type'] or p.get('native_artifact_sha256')!=release['artifact_sha256']
                    or any(k not in p or not field_equal(p[k],v,k) for k,v in node['properties'].items())):bad.append(nid)
            expected_edges={(a,b,t,release['native_revision']) for a,b,t in release['edge_keys']}
            record('native_fields_survive_neo4j_publication',not bad and set(observed)==set(release['nodes_by_id']),
                nodes=len(observed),invalid_or_missing_ids=bad)
            record('native_directed_relations_survive_neo4j_publication',edges==expected_edges,
                expected=len(expected_edges),actual=len(edges),all_graph_reads_from_neo4j=all(v=='neo4j' for v in storages))
            record('all_graph_reads_use_neo4j',bool(storages) and all(v=='neo4j' for v in storages))
    report={'date':datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),'corpus_version':kb.version,
        'container':container,'base_url':base_url,'checks':checks,'passed':all(c['passed'] for c in checks),
        'native_graph_checked':native,'files_sha256':expected,'scope':'actual current container code/data hashes and optionally every available native graph via public API; no inference implied'}
    target=ROOT/'outputs'/output
    if not target.resolve().is_relative_to((ROOT/'outputs').resolve()):raise ValueError('Output must remain under outputs')
    target.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'passed':report['passed'],'files':len(files),'checks':checks},ensure_ascii=False))
    return report['passed']


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--container',required=True);parser.add_argument('--base-url',required=True)
    parser.add_argument('--container-root',choices=['/app','/icp'],default='/app');parser.add_argument('--native',action='store_true');parser.add_argument('--output',default='runtime-source-verification.json')
    args=parser.parse_args()
    try:raise SystemExit(not main(args.container,args.base_url,args.container_root,args.native,args.output))
    except Exception as exc:print('Runtime verification stopped; error type='+type(exc).__name__);raise SystemExit(1)
