"""Source-backed function search text; does not alter the canonical knowledge facts."""
import hashlib
import json


def function_index_text(kb,record):
    fields=record['fields'];module=kb.records.get(fields.get('module_id'))
    evidence=list(record['evidence_ids'])
    lines=[fields['entity_type']+'：'+record['name'], '功能描述：'+fields['description']]
    if module:
        evidence+=module['evidence_ids']
        lines+=['实现模块：'+module['fields']['alias']+' / '+module['name'],
                '模块行为：'+module['fields']['description'],
                '原始现象表述：'+module['fields']['expressions']]
    evidence=list(dict.fromkeys(evidence))
    content='\n'.join(lines)+'\n'+f"[CORPUS_VERSION={kb.version}] [RECORD_ID={record['id']}] "+' '.join('[SOURCE_ID='+x+']' for x in evidence)
    return content,evidence


def profile_digest(kb):
    content=[function_index_text(kb,r)[0] for r in kb.by_kind['function']]
    return hashlib.sha256(json.dumps(content,ensure_ascii=False).encode()).hexdigest()
