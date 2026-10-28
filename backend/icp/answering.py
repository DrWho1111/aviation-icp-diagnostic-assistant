"""Readable excerpts and bounded evidence packets; index metadata is not an answer."""
import json
import re


def readable_text(content):
    text=str(content).split('\n[SOURCE_METADATA=',1)[0]
    text=re.sub(r'\[(?:CORPUS_VERSION|RECORD_ID|SOURCE_ID)=[^\]]*\]','',text).strip()
    start=text.find('{')
    if start>=0:
        try:
            value,_=json.JSONDecoder().raw_decode(text[start:])
            if isinstance(value,dict) and isinstance(value.get('content'),str):text=value['content']
        except (ValueError,TypeError):pass
    text=text.replace('\\n','\n').replace('\\t',' ')
    return re.sub(r'\n{3,}','\n\n',text).strip()


def evidence_packet(evidence):
    packet=[];remaining=6500
    for e in evidence[:8]:
        if remaining<150:break
        text=readable_text(e['raw_text'])[:min(1200,remaining)];remaining-=len(text)
        packet.append({'id':e['id'],'document':e['document'],'location':e.get('section') or ('表'+str(e['table'])+'行'+str(e['row']) if e.get('table') else ''),'text':text})
    return packet


def validate_grounded(value,evidence):
    allowed={e['id'] for e in evidence}
    if not isinstance(value,dict) or not isinstance(value.get('answer'),str) or not value['answer'].strip():raise ValueError('Answer schema')
    cited=value.get('citations')
    if not isinstance(cited,list) or any(not isinstance(e,str) or e not in allowed for e in cited):raise ValueError('Unknown citation')
    if not isinstance(value.get('sufficient'),bool):raise ValueError('Evidence decision missing')
    if value['sufficient'] and not cited:raise ValueError('Missing citation')
    if re.search(r'CORPUS_VERSION|SOURCE_METADATA|\[RECORD_ID=|<think>|\\n',value['answer']):raise ValueError('Index content is not a final answer')
    if re.search(r'MFL\s*(?:的)?\s*(?:HMC故障码|健康管理码|故障代码|故障码)',value['answer'],re.I):raise ValueError('MFL records are not HMC codes')
    if re.search(r'(?:本次|此次|这次|当前)(?:故障)?的?(?:根因|原因)\s*(?:是|为|已确认|已确定)',value['answer']):
        raise ValueError('Retrieved background or historical evidence cannot confirm the current incident cause')
    if re.search(r'(?<!不能)(?<!无法)(?<!不)(?:确保|保证|必然|一定)[^。；\n]{0,24}(?:持续运行|正常运作|正常运行|不中断|不会中断)',value['answer']):
        raise ValueError('Redundancy cannot guarantee uninterrupted operation')
    if re.search(r'冗余|备用通道',value['answer']) and re.search(r'(?<!不能)(?<!无法)(?<!不)(?:确保|保证|必然|一定)',value['answer']):
        raise ValueError('A redundancy design is not a guaranteed fault outcome')
    source_text='\n'.join(str(e.get('text',e.get('raw_text',''))) for e in evidence)
    if '自动切换' in value['answer'] and '自动切换' not in source_text:
        raise ValueError('Automatic switching is absent from the supplied sources')
    # The handbooks name HMC fault codes without defining other acronym expansions.
    for expansion in re.findall(r'(?<![A-Za-z])HMC\s*[（(]([^）)]+)[）)]',value['answer'],re.I):
        if expansion.strip() not in {'故障码','故障代码','健康管理码'}:raise ValueError('Unsupported HMC terminology')
    source=re.sub('[^a-z]','',json.dumps(evidence,ensure_ascii=False).lower())
    definitions={'RBD':'reliabilityblockdiagram','FTA':'faulttreeanalysis','ICP':'integratedcoreprocessor',
        'FMECA':'failuremodeeffectsandcriticalityanalysis'}
    for acronym,expansion in re.findall(r'\b([A-Z][A-Z0-9-]{1,9})[^\n()（）A-Za-z]{0,24}[（(]([A-Za-z][A-Za-z ,/-]{4,80})[）)]',value['answer']):
        normalized=re.sub('[^a-z]','',expansion.lower())
        if definitions.get(acronym)!=normalized and normalized not in source:
            raise ValueError('English acronym expansion is absent from the supplied sources')
    embedded=set(re.findall(r'DOC-\d{3}-(?:T\d+-R\d+|S\d+)',value['answer']))
    if not embedded.issubset(allowed):raise ValueError('Unknown inline citation')
    answer=value['answer'].strip()
    if value['sufficient'] and not any(e in answer for e in cited):answer+='\n\n依据：'+ '、'.join(dict.fromkeys(cited))
    return {'answer':answer,'citations':list(dict.fromkeys(cited)),'sufficient':value['sufficient']}
