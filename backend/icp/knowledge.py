from __future__ import annotations

from collections import Counter
import json
import math
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]


def tokens(value: str) -> list[str]:
    value = value.lower()
    words = re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)*", value)
    for segment in re.findall(r"[\u4e00-\u9fff]+", value):
        words.extend(segment[i:i+2] for i in range(max(1, len(segment)-1)))
    return words


class Knowledge:
    def __init__(self, path: Path | None = None, *, use_database=False):
        self.data = json.loads((path or ROOT / "data/processed/knowledge.json").read_text(encoding="utf-8"))
        self.version = self.data["version"]
        if use_database:
            import os
            from sqlalchemy import create_engine, text as sql
            try:
                engine=create_engine(os.environ["DATABASE_URL"])
                with engine.connect() as connection:
                    rows=connection.execute(sql("SELECT kind,payload FROM icp_knowledge WHERE version=:version"),{"version":self.version}).all()
                engine.dispose()
                source_records={r["id"]:r for kind,r in rows if kind not in {"source","evidence"}}
                if set(source_records)!={r["id"] for r in self.data["records"]}: raise ValueError("版本数据未完整发布")
                self.data["records"]=[source_records[r["id"]] for r in self.data["records"]]
                self.data["documents"]=[r for kind,r in rows if kind=="source"]
                self.data["evidence"]={r["id"]:r for kind,r in rows if kind=="evidence"}
            except Exception:
                raise RuntimeError("当前知识版本尚未完整发布到PostgreSQL，或数据库连接失败") from None
        self.records = {r["id"]: r for r in self.data["records"]}
        self.by_kind = {k: [r for r in self.records.values() if r["kind"] == k] for k in self.data["counts"]}
        self._terms = {r["id"]: Counter(tokens(r["id"] + " " + r["name"]*3 + " " + json.dumps(r["fields"], ensure_ascii=False))) for r in self.records.values()}
        self._df = Counter(t for terms in self._terms.values() for t in terms)
        self._avg_length = sum(sum(t.values()) for t in self._terms.values()) / max(1, len(self.records))

    def search(self, query: str, kinds: list[str] | None = None, limit: int = 6, corpus: str | None = "synthetic") -> list[dict]:
        query_terms = set(tokens(query))
        n = len(self.records)
        results = []
        for record in self.records.values():
            if kinds and record["kind"] not in kinds or corpus and record["corpus"] != corpus:
                continue
            terms = self._terms[record["id"]]
            length = sum(terms.values())
            score = 0.0
            for term in query_terms:
                frequency = terms.get(term, 0)
                if frequency:
                    idf = math.log(1 + (n-self._df[term]+.5)/(self._df[term]+.5))
                    score += idf * frequency * 2.2 / (frequency+1.2*(.25+.75*length/self._avg_length))
            for literal in [record["id"], record["name"]]:
                if literal.lower() in query.lower():
                    score += 30
            if score:
                results.append({**record, "score": round(score, 4)})
        return sorted(results, key=lambda r: (-r["score"], r["id"]))[:limit]

    def locate(self, query: str) -> list[dict]:
        ranked = self.search(query, ["function"], 30)
        generic = set(tokens("系统功能处理通道数据模式输出控制任务异常"))
        specific = set(tokens(query)) - generic
        for record in ranked:
            record["score"] += 12 * len(specific & set(tokens(record["name"])))
        ranked.sort(key=lambda r: (-r["score"], r["id"]))
        # Parent records are context, not competing candidates when matching leaf records exist.
        leaves = [r for r in ranked if r["fields"]["parent_id"]]
        return (leaves or ranked)[:3]

    def model_for_function(self, function_id: str, kind: str) -> list[dict]:
        record = self.records.get(function_id)
        if not record or record["kind"] != "function":
            return []
        f = record["fields"]
        ids = {function_id, f.get("parent_id")}
        alias = (f.get("aliases") or "").split(" ")[0]
        models = []
        for model in self.by_kind[kind]:
            if kind == "fta":
                covered = any(i and i in model["fields"].get("关联功能", "") for i in ids)
            else:
                covered = alias in model["fields"]["rates"] if alias else False
                if not f.get("parent_id"):
                    domain = {"FUNC-100": 1, "FUNC-200": 2, "FUNC-300": 3, "FUNC-400": 4}.get(function_id)
                    covered = bool(domain and model["id"] == f"RBD-SYN-{domain:03d}")
            if covered:
                models.append(model)
        return models

    def evidences(self, ids: list[str]) -> list[dict]:
        return [self.data["evidence"][i] for i in dict.fromkeys(ids) if i in self.data["evidence"]]

