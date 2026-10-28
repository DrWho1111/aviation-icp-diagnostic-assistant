"""Record empty dedicated databases, then publish and verify candidate knowledge."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import subprocess
import sys
import time
from urllib.parse import quote
from zoneinfo import ZoneInfo
from dotenv import dotenv_values
from sqlalchemy import create_engine,text
from neo4j import GraphDatabase

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.configure_platforms import replace_env


def main(workspace,project='icp-clean-workbench'):
    workspace=workspace.resolve();manifest=workspace/'REBUILD-MANIFEST.json'
    if workspace==ROOT or not manifest.is_file():raise ValueError('Select only the extracted candidate workspace')
    settings=dotenv_values(workspace/'.env')
    url='postgresql+psycopg://icp:'+quote(settings['POSTGRES_PASSWORD'],safe='')+'@127.0.0.1:15433/icp'
    db=create_engine(url);graph=GraphDatabase.driver('bolt://127.0.0.1:17687',auth=('neo4j',settings['NEO4J_PASSWORD']))
    marker=workspace/'.secrets/isolated-db-initial.json'
    if marker.exists():raise ValueError('Initial database evidence already exists; do not overwrite it')
    deadline=time.monotonic()+90
    while True:
        try:
            with db.connect() as connection:tables=connection.execute(text("select count(*) from information_schema.tables where table_schema='public'")).scalar_one()
            with graph.session() as session:nodes=session.run('MATCH(n) RETURN count(n) AS total').single()['total']
            break
        except Exception:
            if time.monotonic()>deadline:raise RuntimeError('Isolated databases not ready') from None
            time.sleep(1)
    if tables or nodes:raise ValueError('Databases are not empty; stopped without replacing data')
    import re
    if not re.fullmatch(r'icp-clean-[a-z0-9-]+',project):raise ValueError('Isolated project name required')
    initial={'postgres_public_tables':tables,'neo4j_nodes':nodes,'empty':True,'project':project}
    marker.write_text(json.dumps(initial,indent=2),encoding='utf-8')
    changes={'DATABASE_URL':url,'NEO4J_URI':'bolt://127.0.0.1:17687','NEO4J_USER':'neo4j','READ_KB_FROM_POSTGRES':'true',
        'GRAPH_BACKEND':'neo4j','DIFY_BASE_URL':'http://127.0.0.1:18081/v1','RAGFLOW_BASE_URL':'http://127.0.0.1:19380'}
    env=workspace/'.env';env.write_text(replace_env(env.read_text(encoding='utf-8'),changes),encoding='utf-8')
    published=subprocess.run([sys.executable,'scripts/publish.py','--postgres','--neo4j'],cwd=workspace,capture_output=True,text=True,encoding='utf-8')
    if published.returncode:raise RuntimeError('Candidate database publication failed; no live database was touched')
    kb=json.loads((workspace/'data/processed/knowledge.json').read_text(encoding='utf-8'))
    with db.connect() as connection:
        versions=connection.execute(text('select version,count(*) from icp_knowledge group by version')).all()
    with graph.session() as session:
        identities=session.run('MATCH(n:ICPNode) RETURN count(n) AS total,count(distinct n.version) AS versions').single().data()
        event=session.run("MATCH(n:ICPNode {version:$v,id:'EVT-SYN-001-BE-01'}) RETURN n.properties_json AS properties",v=kb['version']).single()
    source_event=next(r for r in kb['records'] if r['id']=='FTA-SYN-001')['fields']['events'][0]
    actual=json.loads(event['properties'])
    checks=[{'name':'empty_databases_recorded','passed':True},
        {'name':'only_candidate_knowledge_version_published','passed':len(versions)==1 and versions[0][0]==kb['version']},
        {'name':'neo4j_has_only_one_candidate_version','passed':identities['total']>0 and identities['versions']==1},
        {'name':'engineering_probability_survives_reference_merge','passed':actual['probability']==source_event['probability']}]
    report={'date':datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),'corpus_version':kb['version'],'workspace':str(workspace),'initial':initial,
        'checks':checks,'passed':all(c['passed'] for c in checks),'postgres_rows':versions[0][1],'neo4j_nodes':identities['total'],
        'scope':'new PostgreSQL/Neo4j volumes and candidate publication; complete Dify/RAGFlow rebuild remains a separate stage'}
    (ROOT/'outputs/isolated-database-verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    db.dispose();graph.close();print(json.dumps({k:report[k] for k in ['passed','corpus_version','postgres_rows','neo4j_nodes']}))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--workspace',type=Path,required=True);parser.add_argument('--project',default='icp-clean-workbench')
    try:
        args=parser.parse_args();main(args.workspace,args.project)
    except Exception as exc:print('Isolated database verification stopped; error type='+type(exc).__name__);raise SystemExit(1)
