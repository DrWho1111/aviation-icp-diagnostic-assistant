"""Repeatable ingestion of original ZIP, DOCX tables, PDF pages, and source assets."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import shutil
import zipfile
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def text(node) -> str:
    return "".join(n.text or "" for n in node.findall(".//w:t", NS)).strip()


def docx(content: bytes) -> dict:
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        body = ET.fromstring(z.read("word/document.xml")).find("w:body", NS)
        paragraphs, tables, blocks = [], [], []
        for node in body:
            if node.tag.endswith("}p"):
                value = text(node)
                if value:
                    paragraphs.append(value)
                    blocks.append({"type": "paragraph", "text": value})
            elif node.tag.endswith("}tbl"):
                rows = [[text(cell) for cell in row.findall("w:tc", NS)]
                        for row in node.findall("w:tr", NS)]
                tables.append(rows)
                blocks.append({"type": "table", "table": len(tables), "rows": rows})
        images = {Path(n).name: z.read(n) for n in z.namelist() if n.startswith("word/media/")}
    return {"paragraphs": paragraphs, "tables": tables, "blocks": blocks, "images": images}


def safe_name(info: zipfile.ZipInfo) -> str:
    name = info.filename
    if not info.flag_bits & 0x800:
        try:
            name = name.encode("cp437").decode("gb18030")
        except (UnicodeError, LookupError):
            pass
    name = name.replace("\\", "/")
    parts = PurePosixPath(name).parts
    if name.startswith("/") or any(p in {"..", "."} or ":" in p for p in parts):
        raise ValueError("压缩包包含不安全路径")
    if info.file_size > 100_000_000 or (info.external_attr >> 16) & 0o170000 == 0o120000:
        raise ValueError("压缩包成员过大或为符号链接")
    return name


def load_archive(archive: Path, destination: Path) -> list[dict]:
    entries = []
    def unpack(content: bytes, prefix: str = "", depth: int = 0):
        if depth > 2:
            raise ValueError("压缩包嵌套过深")
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            if len(z.infolist()) > 500 or sum(i.file_size for i in z.infolist()) > 600_000_000:
                raise ValueError("压缩包超出本题数据规模")
            for info in z.infolist():
                name = safe_name(info)
                if info.is_dir():
                    continue
                data = z.read(info)
                logical = prefix + name
                if name.endswith(".zip"):
                    unpack(data, logical + "!", depth+1)
                    continue
                if len(entries) >= 200:
                    raise ValueError("资料数量超出限制")
                sid = f"DOC-{len(entries)+1:03d}"
                path = destination / sid / Path(name).name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                corpus = ("synthetic" if "7类虚拟" in logical else
                          "research" if "ICP相关资料.zip!" in logical else "requirements")
                entries.append({"id": sid, "name": path.name, "archive_path": logical,
                                "path": path.relative_to(ROOT).as_posix(), "sha256": digest(data),
                                "bytes": len(data), "corpus": corpus, "synthetic": corpus == "synthetic",
                                "status": "registered"})
    unpack(archive.read_bytes())
    return entries


def build(archive: Path) -> dict:
    out = ROOT / "data/processed"
    out.mkdir(parents=True, exist_ok=True)
    raw = ROOT / "data/raw"
    raw.mkdir(parents=True, exist_ok=True)
    archive_hash = digest(archive.read_bytes())
    backup = raw / "original.zip"
    if archive.resolve() != backup.resolve():
        shutil.copy2(archive, backup)
    documents = load_archive(backup, raw / "documents")
    records, evidence, chunks = [], {}, []
    parsed = {}

    def add(kind: str, rid: str, name: str, fields: dict, source: dict,
            table: int | None = None, row: int | None = None,
            section: str | None = None, page: int | None = None, raw_text: str = ""):
        eid = f"{source['id']}-" + (f"T{table}-R{row}" if table else f"S{len(evidence)+1}")
        ev = {"id": eid, "document_id": source["id"], "document": source["name"],
              "corpus": source["corpus"], "synthetic": source["synthetic"],
              "table": table, "row": row, "section": section, "page": page,
              "raw_text": raw_text, "file_url": f"/api/sources/{source['id']}"}
        evidence[eid] = ev
        record = {"id": rid, "kind": kind, "name": name, "fields": fields,
                  "evidence_ids": [eid], "synthetic": source["synthetic"],
                  "system_id": "SYN-ICP-9000" if source["synthetic"] else None,
                  "configuration": "CONFIG-A" if source["synthetic"] else None,
                  "version": "V1.0", "corpus": source["corpus"]}
        records.append(record)
        chunks.append({"id": rid, "record_id": rid, "kind": kind, "corpus": source["corpus"],
                       "text": " ".join([rid, name, json.dumps(fields, ensure_ascii=False)]),
                       "evidence_ids": [eid]})
        return record

    for source in documents:
        if source["name"].endswith(".docx"):
            parsed[source["name"]] = (source, docx((ROOT/source["path"]).read_bytes()))
            source["status"] = "parsed"
            media = out / "assets" / source["id"]
            for name, data in parsed[source["name"]][1]["images"].items():
                media.mkdir(parents=True, exist_ok=True)
                (media/name).write_bytes(data)
            source["images"] = [f"/api/assets/{source['id']}/{name}" for name in parsed[source["name"]][1]["images"]]

    source, p = parsed["01_系统架构与功能说明.docx"]
    for i,(section,prefix) in enumerate([
        ("图2-1 总体物理架构","总体架构按系统、子系统和模块三级组织"),
        ("图4-1 系统功能树","顶层功能用于描述系统任务能力"),
        ("图6-1 主要数据流、供电与支撑关系","业务数据从外部总线或网络进入IFC")],1):
        description=next(s for s in p["paragraphs"] if s.startswith(prefix))
        eid=f"{source['id']}-ARCH-{i}"
        evidence[eid]={"id":eid,"document_id":source["id"],"document":source["name"],"corpus":"synthetic",
                       "synthetic":True,"section":section,"table":None,"row":None,"page":None,"raw_text":description,
                       "image_url":source["images"][i-1],"file_url":f"/api/sources/{source['id']}"}
    for rowno, row in enumerate(p["tables"][2][1:], 2):
        add("subsystem", row[0], row[2], {"alias": row[1], "description": row[3], "modules": row[4]}, source, 3, rowno, raw_text=" | ".join(row))
    aliases = {row[3]: row for row in p["tables"][4][1:]}
    for rowno, row in enumerate(p["tables"][3][1:], 2):
        module = aliases.get(row[0], [])
        add("function", row[0], row[2], {"parent_id": None if row[1] == "—" else row[1],
            "description": row[3], "module": row[4], "module_id": module[0] if module else None,
            "aliases": (module[1] + " FPGA" if module and module[1] == "FPA" else module[1] if module else ""),
            "expressions": module[6] if module else "", "entity_type": "子功能" if module else "顶层功能"},
            source, 4, rowno, raw_text=" | ".join(row))
    for rowno, row in enumerate(p["tables"][4][1:], 2):
        add("module", row[0], row[2], {"alias": row[1], "function_id": row[3], "description": row[5], "expressions": row[6]}, source, 5, rowno, raw_text=" | ".join(row))

    source, p = parsed["02_HMC故障代码手册.docx"]
    for ti, table in enumerate(p["tables"], 1):
        for ri, row in enumerate(table, 1):
            if len(row) >= 5 and re.fullmatch(r"\d{8}", row[0]):
                code = "-".join(row[:3])
                add("hmc", code, row[3], dict(zip(["lcn", "hamc", "hsc", "phenomenon", "maintenance"], row)), source, ti, ri, raw_text=" | ".join(row))

    source, p = parsed["03_FMECA分析报告.docx"]
    keys = ["ordinal", "id", "product_function", "function_name", "mode", "cause", "stage", "local_effect", "final_effect", "severity", "probability_class", "detection", "improvement", "mitigation"]
    for ti in range(3, 9):
        for ri, row in enumerate(p["tables"][ti][1:], 2):
            add("fmeca", row[1], row[4], dict(zip(keys, row)), source, ti+1, ri, raw_text=" | ".join(row))

    source, p = parsed["06_MFL维修故障清单.docx"]
    fmeca = [r for r in records if r["kind"] == "fmeca"]
    keys = ["id", "module_mode", "board_mode", "circuit_mode", "direct_report", "api_name", "api_call_method", "api_usage", "validation_status", "maintenance"]
    for ri, row in enumerate(p["tables"][2][1:], 2):
        fields = dict(zip(keys, row))
        matched = [r for r in fmeca if row[1].split("：", 1)[-1] == r["name"]]
        fields["failure_mode_ids"] = [r["id"] for r in matched]
        fields["device_api_executable"] = False
        add("mfl", row[0], row[1], fields, source, 3, ri, raw_text=" | ".join(row))

    source, p = parsed["05_RBD可靠性框图分析报告.docx"]
    expressions = [s.split("：", 1)[1] for s in p["paragraphs"] if s.startswith("结构表达式：")]
    titles = [s.split(" ", 1)[1] for s in p["paragraphs"] if re.match(r"^[2-5] \S.*模型$", s)]
    criteria = [s.split("：", 1)[1] for s in p["paragraphs"] if s.startswith("系统成功判据：")]
    for index, expression in enumerate(expressions):
        rates_table = p["tables"][2+index*2]
        expected_table = p["tables"][3+index*2]
        fields = {"expression": expression, "criterion": criteria[index],
                  "rates": {row[1]: float(row[3]) for row in rates_table[1:]},
                  "rates_unit": "1/h", "mission_time_unit": "h",
                  "parameters": rates_table, "expected": expected_table,
                  "image_url": source["images"][index],
                  "id_origin": "故障历史关联索引中的模型编号，通过标题匹配；RBD正文无该编号",
                  "assumption": "单元独立、恒定失效率、指数寿命模型；不含共因和切换失效"}
        add("rbd", f"RBD-SYN-{index+1:03d}", titles[index], fields, source,
            section=f"{index+2} {titles[index]}；参数表{3+index*2}、计算结果表{4+index*2}",
            raw_text="\n".join([criteria[index], expression] + [" | ".join(r) for r in rates_table+expected_table]))

    source, p = parsed["04_FTA故障树分析报告.docx"]
    probability_values = [float(re.search(r"近似概率为\s*([\deE.+-]+)", s).group(1))
                          for s in p["paragraphs"] if "近似概率为" in s]
    for index in range(8):
        meta = dict(p["tables"][2+index*3][1:])
        events = [dict(zip(["id", "failure_mode_id", "module", "name", "cause", "probability"], row)) for row in p["tables"][3+index*3][1:]]
        for event in events:
            event["probability"] = float(event["probability"])
        cuts = []
        for row in p["tables"][4+index*3][1:]:
            group = []
            for item in row[2].split("∧"):
                module, mode = item.strip().split(":", 1)
                matched = [e for e in events if e["module"] == module and e["name"] == mode]
                if len(matched) != 1:
                    raise ValueError(f"FTA割集关联不唯一：{item}")
                group.append(matched[0]["id"])
            cuts.append(group)
        fields = {**meta, "events": events, "minimal_cuts": cuts,
                  "source_probability": probability_values[index], "image_url": source["images"][index],
                  "assumption": "唯一底事件独立；概率为合成无量纲数值，不代表诊断置信度"}
        add("fta", meta["故障树编号"], meta["顶事件"], fields, source,
            section=f"{index+2} {meta['顶事件']}；模型表{3+index*3}、底事件表{4+index*3}、割集表{5+index*3}",
            raw_text="\n".join([" | ".join(row) for table in p["tables"][2+index*3:5+index*3] for row in table]))

    source, p = parsed["07_故障历史查询文档.docx"]
    active = None
    cases = []
    for block in p["blocks"]:
        if block["type"] == "paragraph":
            heading = re.match(r"2\.\d+\s+(HIS-SYN-\d{3})\s+(.*)", block["text"])
            if heading:
                if active:
                    cases.append(active)
                active = {"id": heading[1], "name": heading[2], "lines": [], "table": None}
            elif re.match(r"3\s+", block["text"]):
                if active:
                    cases.append(active)
                    active = None
            elif active:
                active["lines"].append(block["text"])
        elif active:
            active["table"] = block["table"]
            active["lines"].extend("：".join(row) for row in block["rows"])
    if active:
        cases.append(active)
    for case in cases:
        content = "\n".join(case["lines"])
        fields = {"content": content, "function_ids": sorted(set(re.findall(r"FUNC-\d+", content))),
                  "failure_mode_ids": sorted(set(re.findall(r"(?<![A-Z])FM-SYN-\d+", content))),
                  "fmeca_aliases": sorted(set(re.findall(r"FMECA-SYN-\d+", content))),
                  "hmc_codes": sorted(set(re.findall(r"\d{8}-(?:\*\*|\d{2})-\d{4}", content))),
                  "fta_covered": "FTA→当前8棵FTA中未单独建模" not in content,
                  "rbd_covered": "RBD→当前4个RBD模型中未" not in content,
                  "confirmed_cause": next((x.split("：", 1)[1] for x in case["lines"] if x.startswith("故障原因分析：")), ""),
                  "solution": next((x.split("：", 1)[1] for x in case["lines"] if x.startswith("最终解决方案：")), "")}
        add("history", case["id"], case["name"], fields, source, section=case["id"], raw_text=content)

    issues = [{"code": "PFL_NOT_PROVIDED", "description": "原始资料未提供PFL，保留入口并返回not_covered"},
              {"code": "SYSTEM_REQUIREMENTS_NOT_PROVIDED", "description": "不将燃油示例或项目技术协议当作设备需求实体"}]
    from .ocr_supplements import load_pages, supplement_digest
    supplement_path=out/'ocr-reviewed.json'
    supplements=load_pages(supplement_path,documents)
    # PDF extraction uses physical page numbers. Do not index requirements or example prompts.
    import pymupdf
    for source in documents:
        if source["corpus"] != "research":
            continue
        path = ROOT / source["path"]
        if path.suffix.lower() == ".txt":
            source["status"] = "link_only"
            issues.append({"code": "LINK_ONLY", "document_id": source["id"], "description": "只有百科网址，尚未获取正文"})
            continue
        with pymupdf.open(path) as pdf:
            source["pages"] = len(pdf)
            for page_index, page in enumerate(pdf):
                blocks = page.get_text("blocks", sort=True)
                # The supplied 2018 paper contains a second unrelated article below a rule.
                cutoff = page.rect.height
                if "2018_" in path.name and page_index == 0:
                    rules = [d["rect"].y0 for d in page.get_drawings()
                             if d["rect"].width > page.rect.width * .6 and d["rect"].height < 3
                             and d["rect"].y0 > page.rect.height * .4]
                    if rules:
                        cutoff = max(rules)
                        issues.append({"code": "PDF_CROP_PATCH", "document_id": source["id"], "page": 1,
                                       "description": "排除第一页分隔线下的非本篇内容", "cutoff_y": cutoff})
                page_text = "\n".join(b[4] for b in blocks if b[1] < cutoff).strip()
                supplement=supplements.get((source['id'],page_index+1))
                if supplement:
                    page_text=supplement['text']
                    source.setdefault('reviewed_ocr_pages',[]).append(page_index+1)
                    issues.append({'code':'OCR_PAGE_REVIEWED','document_id':source['id'],'page':page_index+1,
                                   'description':supplement['note']})
                if len(page_text) < 40:
                    issues.append({"code": "LOW_TEXT_PAGE", "document_id": source["id"], "page": page_index+1,
                                   "description": "文字稀少；保留原页，待判断是否需要OCR"})
                for start in range(0, len(page_text), 1400):
                    value = page_text[start:start+1600]
                    if value:
                        add("research", f"{source['id']}-P{page_index+1}-{start}", source["name"], {"content": value}, source,
                            page=page_index+1, section=f"物理页{page_index+1}", raw_text=value)
            source["status"] = "text_extracted"

    counts = {kind: sum(r["kind"] == kind for r in records) for kind in {r["kind"] for r in records}}
    expected = {"subsystem": 6, "module": 24, "function": 30, "hmc": 100, "fmeca": 72, "mfl": 72, "history": 24, "fta": 8, "rbd": 4}
    for kind, count in expected.items():
        if counts.get(kind) != count:
            raise ValueError(f"数据基线不符：{kind}实际{counts.get(kind)}，期望{count}")
    if len({r["id"] for r in records}) != len(records):
        raise ValueError("记录ID重复")
    pipeline_hash = digest(Path(__file__).read_bytes()+(Path(__file__).with_name("graphs.py")).read_bytes()
                           +Path(__file__).with_name('ocr_supplements.py').read_bytes()+supplement_digest(supplement_path))
    corpus_version = "kb-" + archive_hash[:12] + "-" + pipeline_hash[:8]
    result = {"version": corpus_version, "archive_sha256": archive_hash, "pipeline_sha256":pipeline_hash, "documents": documents,
              "records": records, "evidence": evidence, "chunks": chunks, "counts": counts, "issues": issues}
    from types import SimpleNamespace
    from .graphs import export_graphs
    projection = SimpleNamespace(data=result, version=corpus_version, records={r["id"]:r for r in records},
                                 by_kind={kind:[r for r in records if r["kind"]==kind] for kind in counts})
    result["graphs"], result["entity_chunks"] = export_graphs(projection)
    (out / "knowledge.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    data = build(args.archive)
    print(json.dumps({"version": data["version"], "counts": data["counts"], "documents": len(data["documents"]), "issues": len(data["issues"])}, ensure_ascii=False))
