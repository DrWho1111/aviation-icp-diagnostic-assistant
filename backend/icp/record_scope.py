"""Apply only source-backed module, subsystem and severity filters to record lists."""
import re

DOMAINS=[('PWR','电源|配电|供电'),('GPP','通用处理|处理器|处理子系统'),
         ('IFC','接口|通信'),('DSP','显示'),('HMS','健康监测|记录子系统'),('TCS','时钟|热管理|风扇')]


def module_for(kb,record):
    kind=record['kind'];fields=record['fields']
    if kind=='module': return record
    if kind=='function':return kb.records.get(fields.get('module_id'))
    if kind=='history':
        functions=fields.get('function_ids',[])
        return module_for(kb,kb.records[functions[0]]) if len(functions)==1 and functions[0] in kb.records else None
    for module in kb.by_kind['module']:
        alias=module['fields']['alias']
        if kind=='hmc' and re.match(re.escape(alias)+r'(?![A-Za-z0-9-])',record['name'],re.I):return module
        if kind=='fmeca' and fields.get('product_function','').split(' / ')[0]==alias:return module
        if kind=='mfl' and fields.get('module_mode','').startswith(module['name']+'：'):return module
    return None


def record_domain(kb,record):
    if record['kind']=='subsystem':return record['id'].removeprefix('SUB-')
    module=module_for(kb,record)
    if module is None and record['kind']=='function':
        children=[m for f in kb.by_kind['function'] if f['fields']['parent_id']==record['id']
                  for m in [module_for(kb,f)] if m]
        domains={m['id'].split('-')[1] for m in children}
        return next(iter(domains)) if len(domains)==1 else None
    return module['id'].split('-')[1] if module else None


def scope_records(kb,query,kind,pool):
    supported={'hmc','mfl','fmeca','history','module','function','subsystem'}
    filters={}
    if kind not in supported:return pool,filters
    domains=[alias for alias,pattern in DOMAINS if re.search(pattern,query) or re.search(r'(?<![A-Za-z])'+alias+r'(?![A-Za-z])',query,re.I)]
    # "请显示全部清单" is an instruction to render, not a display-subsystem filter.
    if re.search(r'(?:请|给我)显示(?:全部|所有|一下|清单|列表)',query):domains=[x for x in domains if x!='DSP']
    modules=[m for m in kb.by_kind['module'] if m['name'] in query or m['id'].lower() in query.lower()
             or re.search(r'(?<![A-Za-z0-9])'+re.escape(m['fields']['alias'])+r'(?![A-Za-z0-9])',query,re.I)]
    if domains:
        filters['subsystems']=domains
        pool=[r for r in pool if record_domain(kb,r) in domains]
    if modules:
        ids={m['id'] for m in modules};filters['modules']=sorted(ids)
        pool=[r for r in pool if (module_for(kb,r) or {}).get('id') in ids]
    severity=re.search(r'严重度\s*(?:为|是|等于|=|：|:)?\s*(III|II|IV|I|Ⅰ|Ⅱ|Ⅲ|Ⅳ)(?![IV])',query,re.I)
    if severity and kind=='fmeca':
        level={'Ⅰ':'I','Ⅱ':'II','Ⅲ':'III','Ⅳ':'IV'}.get(severity[1],severity[1].upper())
        filters['severity']=level
        pool=[r for r in pool if r['fields']['severity']==level]
    if not domains and not modules:
        # Unknown explicit equipment scope must not return an unrelated global list.
        prefix=re.split(r'HMC|MFL|FMECA|健康管理码|故障代码|故障码|失效模式|历史案例|历史故障|子系统|模块|功能',query,flags=re.I)[0]
        prefix=re.sub(r'列一下|列一列|告诉我|介绍下|说明一下|帮忙|展示一下|展示|目前|我们这个项目|这个项目|系统里面|系统里|项目里面|本平台|平台|项目','',prefix)
        if re.search(r'(?:请|给我)显示(?:全部|所有|一下|清单|列表)',query):prefix=re.sub('显示','',prefix)
        prefix=re.sub(r'请问|请|帮我|给我|我想|想|能不能|能否|可以|能|先|看看|看一下|了解一下|了解|介绍一下|介绍|解释|讲讲|列出|列举|全部|所有|有哪些|有什么|有啥|我们|这个|当前|本项目|系统|知识库|严重度\s*(?:为|是|=)?\s*(?:III|II|IV|I|Ⅰ|Ⅱ|Ⅲ|Ⅳ)|的|相关|部分|[，,。？?\s]','',prefix,flags=re.I)
        prefix=re.sub(r'一共|多少|几条|几种|共有|总共','',prefix)
        if len(prefix)>=2:
            filters['text_scope']=prefix
            ranked=kb.search(prefix,[kind],1000);ids={r['id'] for r in ranked}
            pool=[r for r in pool if r['id'] in ids]
    return pool,filters
