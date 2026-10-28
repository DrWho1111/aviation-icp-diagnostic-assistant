"""Idempotent publication to project PostgreSQL and Neo4j; no credentials in output."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"backend"))
from dotenv import load_dotenv
from sqlalchemy import Column,JSON,MetaData,String,Table,create_engine,delete,insert
from neo4j import GraphDatabase
from icp.knowledge import Knowledge
from icp.native_graph_artifact import load_release,build_publication_graph


def publish(postgres=False,neo4j=False):
    load_dotenv(ROOT/".env")
    kb=Knowledge()
    if postgres:
        url=os.getenv("DATABASE_URL","")
        if not url.startswith("postgresql"):
            raise ValueError("PostgreSQL DATABASE_URL未配置")
        engine=create_engine(url)
        metadata=MetaData()
        table=Table("icp_knowledge",metadata,Column("version",String(64),primary_key=True),Column("id",String(128),primary_key=True),Column("kind",String(32)),Column("payload",JSON,nullable=False))
        metadata.create_all(engine)
        values=[{"version":kb.version,"id":r["id"],"kind":r["kind"],"payload":r} for r in kb.records.values()]
        values += [{"version":kb.version,"id":"SOURCE-"+d["id"],"kind":"source","payload":d} for d in kb.data["documents"]]
        values += [{"version":kb.version,"id":"EVIDENCE-"+e["id"],"kind":"evidence","payload":e} for e in kb.data["evidence"].values()]
        with engine.begin() as connection:
            # Only replace this project's current version inside its own named table.
            connection.execute(delete(table).where(table.c.version==kb.version))
            connection.execute(insert(table),values)
        engine.dispose()
        print("PostgreSQL project knowledge published; version="+kb.version)
    if neo4j:
        password=os.getenv("NEO4J_PASSWORD","")
        if not password:
            raise ValueError("Neo4j凭据未配置")
        native=load_release(kb,ROOT/'data/processed/native-graph-release.json')
        publication=build_publication_graph(kb,native)
        relations=publication['edges']
        values=[{'id':n['id'],'name':n['label'],'entity_type':n['type'],'evidence_ids':n['evidence_ids'],
                 'properties_json':json.dumps(n['properties'],ensure_ascii=False)} for n in publication['nodes']]
        driver=GraphDatabase.driver(os.getenv("NEO4J_URI","bolt://127.0.0.1:7687"),auth=(os.getenv("NEO4J_USER","neo4j"),password))
        with driver.session() as session:
            session.run("CREATE CONSTRAINT icp_identity IF NOT EXISTS FOR (n:ICPNode) REQUIRE (n.version,n.id) IS UNIQUE").consume()
            def write(tx):
                tx.run("UNWIND $nodes AS v MERGE (n:ICPNode {version:$version,id:v.id}) SET n += v",nodes=values,version=kb.version).consume()
                tx.run("UNWIND $edges AS e MATCH (a:ICPNode {version:$version,id:e.source}), (b:ICPNode {version:$version,id:e.target}) MERGE (a)-[r:ICP_RELATION {kind:e.type}]->(b) SET r.evidence_ids=e.evidence_ids, r.derivation=e.derivation, r.extraction_origin=e.extraction_origin, r.native_revision=e.native_revision",edges=relations,version=kb.version).consume()
            session.execute_write(write)
        driver.close()
        print("Neo4j project graph published; version="+kb.version)
        if native:print('Verified native extraction artifact published; revision='+native['native_revision'])


if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--postgres",action="store_true");p.add_argument("--neo4j",action="store_true");args=p.parse_args()
    if not args.postgres and not args.neo4j: p.error("select --postgres and/or --neo4j")
    try:
        publish(args.postgres,args.neo4j)
    except Exception as exc:
        print("Publication failed; check service availability and private configuration. Error type="+type(exc).__name__)
        raise SystemExit(1)

