import json
import os

from neo4j import GraphDatabase


def read_graph(projection):
    ids=[n["id"] for n in projection["nodes"]]
    try:
        with GraphDatabase.driver(os.environ["NEO4J_URI"],auth=(os.getenv("NEO4J_USER","neo4j"),os.environ["NEO4J_PASSWORD"])) as driver:
            with driver.session() as session:
                nodes=session.run("MATCH (n:ICPNode) WHERE n.version=$version AND n.id IN $ids RETURN n",version=projection["corpus_version"],ids=ids).data()
                edges=session.run("MATCH (a:ICPNode)-[r:ICP_RELATION]->(b:ICPNode) WHERE a.version=$version AND b.version=$version AND a.id IN $ids AND b.id IN $ids RETURN a.id AS source,b.id AS target,r.kind AS type,r.evidence_ids AS evidence_ids,r.derivation AS derivation,r.extraction_origin AS extraction_origin,r.native_revision AS native_revision",version=projection["corpus_version"],ids=ids).data()
        if len(nodes)!=len(ids): raise ValueError("图谱未完整发布")
        normalized=[{"id":row["n"]["id"],"label":row["n"]["name"],"type":row["n"]["entity_type"],"properties":json.loads(row["n"].get("properties_json","{}")),"evidence_ids":row["n"].get("evidence_ids",[])} for row in nodes]
        return {**projection,"nodes":normalized,"edges":edges,"storage":"neo4j"}
    except Exception:
        raise RuntimeError("Neo4j当前版本查询失败") from None
