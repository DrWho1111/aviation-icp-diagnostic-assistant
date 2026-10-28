"""Record source-page inspection and audit exported graph structure, without inventing facts."""
from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from icp.knowledge import Knowledge
from icp.graphs import graph_for

BLANK={'DOC-017':[2],'DOC-022':[13,33,69,95,97,99],'DOC-030':[24,38,60]}
FRONT={'DOC-022':[1,5],'DOC-023':[1,2,3,4],'DOC-024':[3],'DOC-030':[1]}
OCR={'DOC-022':[87],'DOC-024':[7,79,80,84,85]}


def main():
    kb=Knowledge();docs={d['id']:d for d in kb.data['documents']}
    now=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()
    report={'date':now,'corpus_version':kb.version,'archive_sha256':kb.data['archive_sha256'],
            'method':'逐页渲染核对；2026-10-24对14页完成DeepDoc OCR及可辨正文校正；原件不变', 'pages':[]}
    supplements={ (p['document_id'],p['page']):p for p in json.loads((ROOT/'data/processed/ocr-reviewed.json').read_text(encoding='utf-8'))['pages'] }
    seen=set()
    for issue in kb.data['issues']:
        if issue['code'] not in {'LOW_TEXT_PAGE','OCR_PAGE_REVIEWED'}:continue
        sid=issue['document_id'];page=issue['page']
        if (sid,page) in seen:continue
        seen.add((sid,page))
        if page in BLANK.get(sid,[]):
            classification='blank_or_header';note='已核对：空白页或仅页眉页码，无实质正文；保留原页。';needed=False
        elif page in FRONT.get(sid,[]):
            classification='front_matter';note='已核对：封面、题名、关键词或声明等前置信息；保留原件；该阶段未登记对应全文索引。';needed=False
        elif page in OCR.get(sid,[]):
            classification='image_text';note='已核对：图像中的图示、摘要或代码文本；该阶段尚缺OCR补充及校对记录。';needed=True
        else:raise RuntimeError('Unreviewed page')
        supplement=supplements.get((sid,page))
        if supplement:
            note='OCR已校正并纳入当前知识文本；'+supplement['note'];needed=False
        source=docs[sid]
        if hashlib.sha256((ROOT/source['path']).read_bytes()).hexdigest()!=source['sha256']:raise RuntimeError('Source changed')
        report['pages'].append({'document_id':sid,'page':page,'classification':classification,'note':note,'ocr_required':needed,'source_sha256':source['sha256']})
    report['counts']=dict(Counter(p['classification'] for p in report['pages']))
    report['passed']=len(report['pages'])==24
    for name in ['data/processed/page_review.json','outputs/source-page-review.json']:
        (ROOT/name).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    engineering=[]
    for kind,ids in [('function',['all']),('fmeca',['all']),('references',['all']),('fta',[r['id'] for r in kb.by_kind['fta']]),('rbd',[r['id'] for r in kb.by_kind['rbd']])]:
        for rid in ids:
            graph=graph_for(kb,kind,rid);nodes={n['id'] for n in graph['nodes']}
            evidence_ok=all(n.get('evidence_ids') and set(n['evidence_ids'])<=set(kb.data['evidence']) for n in graph['nodes'])
            endpoints_ok=all(e.get('source') in nodes and e.get('target') in nodes for e in graph['edges'])
            engineering.append({'kind':kind,'id':rid,'nodes':len(nodes),'edges':len(graph['edges']),'source_ids_valid':evidence_ok,'edge_endpoints_valid':endpoints_ok})
    candidates=[]
    for name in ['function','rbd','fta','fmeca']:
        data=json.loads((ROOT/f'outputs/ragflow-graphs/ICP-{name}.json').read_text(encoding='utf-8'))
        entities=[e for t in data.get('templates',[]) for e in t.get('entities',[])]
        candidates.append({'group':name,'entities':len(entities),'types':dict(Counter(e.get('type') for e in entities)),
            'with_typed_properties_object':sum(isinstance(e.get('properties'),dict) for e in entities),
            'note':'导出以name/type/description为主；description中的属性未完成逐字段语义验收，非空不等于全部原文schema满足。'})
    graph_report={'date':now,'corpus_version':kb.version,'engineering':engineering,'native_candidates':candidates,
                  'checks_completed':True,'strict_all_clauses_satisfied':False,
                  'limitations':['没有PFL或正式设备SystemRequirement正文','原生Graph完整属性与关系语义仍需验收','Dify循环、确认与并行由后端调度，属于工程适配','未完成独立盲测、并发性能和新机器全栈恢复']}
    assert all(x['source_ids_valid'] and x['edge_endpoints_valid'] for x in engineering)
    (ROOT/'outputs/graph-structure-review.json').write_text(json.dumps(graph_report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'pages_reviewed':24,'page_classification':report['counts'],'engineering_graphs':len(engineering),'strict_all_clauses_satisfied':False},ensure_ascii=False))


if __name__=='__main__':main()
