"""Resolve narrow references to an unambiguous object in the same conversation."""
import re
from .parameters import TIME
from .record_scope import module_for


def resolve_followup(question,previous,kb):
    pronoun=re.search(r'^(?:那|那么|所以|还有|请问|请)?(?:它|这个|该模块|该功能|该故障|这个模块|这个功能)',question)
    if not previous or previous.get('status') in {'queued','running','error'}:
        return {'context_unresolved':True,'context_note':'当前没有可唯一接续的对象，请补充模块、功能或编号。'} if pronoun else None
    prior=previous.get('effective_question',previous['question'])
    range_prefix=r'\d+(?:\.\d+)?\s*(?:至|到|~|～|—|–|-)\s*(?=[+-]?\d)'
    remainder=TIME.sub('',re.sub(range_prefix,'',question))
    remainder=re.sub(r'那就|就按|再计算|再算|计算一下|计算|改成|改为|好的|好|那|按|用|算|呢|吧|负|[，,。？?\s]','',remainder)
    models=set(re.findall(r'RBD-SYN-\d{3}',prior,re.I))
    if TIME.search(question) and not remainder and len(models)==1:
        # Keep invalid signs/ranges for the normal parameter checker to reject.
        match=TIME.search(question)
        time_text=question[match.start():match.end()]
        if '负' in question[:match.start()]:time_text='负'+time_text
        if re.search(range_prefix,question):time_text=question
        model=next(iter(models)).upper()
        return {'effective_question':f'计算 {model} 的{time_text}可靠度',
                'context_source_run_id':previous['id'],'context_note':f'已接续上一问的模型 {model}；任务时间以本次输入为准。'}
    if not re.search(r'^(?:那|那么|所以|还有|请问|请)?(?:它|这个|该模块|该功能|该故障|这个模块|这个功能)',question):return None
    # An explicit new identifier is a new query, never carry over a stale object.
    if re.search(r'\d{8}|(?:FUNC|MOD|HIS|FM|MFL|FTA|RBD)-',question,re.I):return None
    data=(previous.get('result') or {}).get('data',[])
    record=kb.records.get(previous.get('selected_function_id'))
    if record is None and isinstance(data,list) and len(data)==1:
        entry=data[0];record=kb.records.get(entry.get('id',entry.get('model',{}).get('id')))
    if record is None:return {'context_unresolved':True,'context_note':'上一问没有唯一对象，请补充模块、功能或编号。'}
    module=module_for(kb,record)
    subject=f"{module['fields']['alias']}（{module['name']}）" if module else f"{record['id']}（{record['name']}）"
    resolved=re.sub(r'^(?:那|那么|所以|还有|请问|请)?(?:这个模块|这个功能|该模块|该功能|该故障|这个|它)',subject,question,count=1)
    return {'effective_question':resolved,'context_source_run_id':previous['id'],'context_note':f'本次“它/这个”按上一问的 {subject} 查询。'}
