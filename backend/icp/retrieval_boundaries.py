"""Conservative guards for navigation pages and explicit concept definitions."""
import re


def navigation_only(text):
    return len(re.findall(r'\.{8,}|…{4,}',text))>=3


def definition_term(question):
    question=question.strip().rstrip('？?。')
    match=re.search(r'什么是([^，。？！?]{2,32})',question)
    if match:term=match[1]
    else:term=None
    match=re.search(r'([^，。？！?]{2,32}?)(?:是什么|是什么意思)',question)
    if match:term=match[1]
    elif term is None:
        match=re.search(r'([^，。？！?]{2,32}?)(?:是干什么|干什么|是做什么|做什么|有什么用|有何用途|负责什么)(?:的)?(?:[？?。]|$)',question)
        if not match:match=re.search(r'(?:解释|介绍)(?:一下)?([^，。？！?]{2,32})',question)
        if not match:return None
        term=match[1]
    term=re.sub(r'^.*(?:中的|里的|领域的)','',term)
    term=re.sub(r'^(?:请问|请|什么是|解释|介绍|一下)+','',term)
    term=re.sub(r'(?:是)?(?:干什么|做什么|干啥|做啥)(?:的)?$|(?:有什么用|有何用途|负责什么)(?:的)?$','',term).strip()
    term=re.sub(r'的(?:含义|概念|定义|作用|意思|功能)$','',term).strip()
    # “ICP的综合处理架构是什么” asks about a supported subject's aspect,
    # not the literal standalone term “ICP的综合处理架构”. Keep the subject
    # guard; retrieval/generation still have to ground the requested aspect.
    term=re.sub(r'的[^的]{0,8}(?:架构|结构|组成|原理)$','',term).strip()
    # A domain qualifier does not create a different acronym. Keep this narrow:
    # an unknown subject such as “航空XYZ” must not turn into a known ICP term.
    if re.fullmatch(r'(?:航空|航电|航空电子)(?:领域的|中的|里的)?\s*ICP',term,re.I):term='ICP'
    return term if 2<=len(term)<=16 else None


def term_in_sources(term,evidence):
    aliases={'RBD':['可靠性框图'],'FTA':['故障树'],'FMECA':['故障模式影响'],'ICP':['综合处理机','综合核心处理机'],
        '功能架构':['functional architecture']}
    candidates=[term,*aliases.get(term.upper(),aliases.get(term,[]))]
    normalize=lambda value:re.sub(r'\s+','',value).casefold()
    sources=normalize('\n'.join(e['raw_text'] for e in evidence))
    def present(candidate):
        candidate=normalize(candidate)
        if re.fullmatch(r'[a-z][a-z0-9-]*',candidate):
            # A model ID such as RBD-SYN-004 is not a definition of RBD.
            if candidate in {'rbd','fta','fmeca'}:
                return bool(re.search(r'(?<![a-z0-9-])'+re.escape(candidate)+r'(?:是|指|用于|表示|称|[（(])',sources))
            return bool(re.search(r'(?<![a-z0-9-])'+re.escape(candidate)+r'(?![a-z0-9-])',sources))
        return candidate in sources
    return any(present(candidate) for candidate in candidates)


def module_subject_records(kb,term):
    """Resolve named modules using source labels and explicit FMECA links only."""
    if not term:return [],[]
    modules=[];support=[]
    for module in kb.by_kind['module']:
        alias=module['fields']['alias']
        named=(term.casefold() in module['name'].casefold() or term.casefold()==alias.casefold())
        linked=[r for r in kb.by_kind['fmeca'] if term.casefold() in r['name'].casefold()
            and re.search(r'(?<![A-Za-z0-9-])'+re.escape(alias)+r'(?![A-Za-z0-9-])',r['fields'].get('product_function',''),re.I)]
        if named or linked:
            modules.append(module);support.extend(linked)
    return modules,support
