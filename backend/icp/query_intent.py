"""Business-topic constraints, separate from the model's natural-language planning."""
import re
from .coverage_boundaries import asks_equipment_requirements,asks_history_proof,unsupported_equipment_target

PATTERNS={
 'hmc':r'(?<![A-Za-z])HMC(?![A-Za-z])|健康管理码|故障代码|故障码',
 'mfl':r'(?<![A-Za-z])MFL(?![A-Za-z])|维修故障清单',
 'fmeca':r'(?<![A-Za-z])FMECA(?![A-Za-z])|失效模式',
 'fta':r'(?<![A-Za-z])FTA(?![A-Za-z])|故障树',
 'rbd':r'(?<![A-Za-z])RBD(?![A-Za-z])|可靠性框图',
 'history':r'历史案例|历史故障|故障历史|排故记录|HIS-SYN-',
 'subsystem':r'子系统', 'module':r'模块', 'function':r'功能',
}
TOOLS={'hmc':'HMC_Query','mfl':'MFL_PFL_Query','fmeca':'FMECA_Query','fta':'FTA_Query','rbd':'RBD_Query','history':'Fault_History_Query'}
LABELS={'hmc':'HMC故障码','mfl':'MFL维修清单','fmeca':'FMECA失效模式','fta':'FTA故障树','rbd':'RBD可靠性模型','history':'历史案例','subsystem':'子系统','module':'模块','function':'功能'}

WORKSPACE_OVERVIEW=('关于本工作台：这是航空ICP知识库问答与故障辅助诊断系统。你可以解释资料中的概念、'
    '查询故障码和维修清单、查看失效影响及历史案例，或计算资料中已有FTA／RBD模型的工程数值。'
    '描述故障现象后，系统先定位功能、请你确认，再并行查询相关资料并列出来源。'
    '当前设备示例是SYN-ICP-9000合成资料，答复用于原型验证，不能代替真实设备的故障确认。')


def workspace_question(question):
    """Separate explicit questions about this application from equipment facts."""
    parts=re.split(r'[，,。；;？?\n]+',question)
    help_parts=[];topics=[]
    for part in parts:
        part=part.strip()
        if not part:continue
        if re.fullmatch(r'(?:请问|请介绍一下|介绍一下)?(?:我们(?:的)?(?:这个)?系统|这个工作台|本工作台|这个诊断系统|这个软件)(?:到底|主要)?(?:是干什么|干什么|是做什么|做什么|有什么用|有什么功能|能做什么|能干什么)(?:的|呢|呀|啊)?',part):help_parts.append(part)
        else:topics.append(part)
    return bool(help_parts),'，'.join(topics)


def diagnostic_intent(question):
    return bool(re.search(r'排查|排故|排错|原因|为什么|怎么修|怎么处理|如何处理|怎么办|咋办|咋整|怎么查|如何查|咋查',question))


def topics(question):
    return [kind for kind,pattern in PATTERNS.items() if re.search(pattern,question,re.I)]


def catalog_kind(question):
    if re.search(r'\d{8}|(?:FM(?:ECA)?|FTA|RBD|MFL|HIS|FUNC|MOD)-SYN-|计算|最小割集|概率是多少',question,re.I):return None
    kinds=topics(question)
    # An explicit engineering acronym is stronger than a generic word such as 功能.
    specific=[k for k in kinds if k in TOOLS]
    kinds=specific or kinds
    if specific and specific!=['hmc'] and re.search(r'是什么|什么是|什么意思|定义|什么作用|有何作用',question) and not re.search(r'清单|目录|多少|几条|全部|所有|有哪些|哪几',question):
        return None
    if not specific and len(kinds)>1:
        kinds=[max(kinds,key=lambda kind:max(m.end() for m in re.finditer(PATTERNS[kind],question,re.I)))]
    if len(kinds)!=1:return None
    if kinds[0]=='fmeca' and re.search(r'影响|后果|原因|阶段|检测|补偿',question) and not re.search(r'清单|目录|多少|几条|全部',question):return None
    if re.search(r'哪些|哪几|有什么|有啥|都有啥|都有.{0,2}什么|列出|列举|清单|列表|目录|概览|多少|几条|几种|全部|所有',question):return kinds[0]
    if kinds[0]=='hmc' and re.search(r'是什么|什么是|介绍|解释|讲讲|分段|构成|编码规则|怎么读|啥意思',question):return 'hmc'
    return None


def constrain_action(question,action,kb=None):
    if action['tool']=='End':return action
    application_help,topic=workspace_question(question)
    if application_help and topic and action['tool'] not in {'Fault_Reasoning_Engine'}:
        action={**action,'query':topic}
    if kb is not None and action['tool']=='Hybrid_Search' and not re.search(r'论文|文献|学术|研究进展',question):
        from .retrieval_boundaries import definition_term,module_subject_records
        term=definition_term(topic if application_help else question)
        # Only a subject explicitly present in the equipment module source can
        # override a planner's generic research choice. Never infer a new alias.
        if module_subject_records(kb,term)[0]:
            action={**action,'query':topic if application_help else question,'corpus':'synthetic','missing_fields':[]}
    if unsupported_equipment_target(question):return {**action,'tool':'Hybrid_Search','query':question,'corpus':'synthetic','missing_fields':[]}
    if action['tool']=='Fault_Reasoning_Engine' and not diagnostic_intent(question):
        action={**action,'tool':'Hybrid_Search','query':question,'corpus':'synthetic','missing_fields':[]}
    if asks_equipment_requirements(question):return {**action,'tool':'Hybrid_Search','query':question,'corpus':'synthetic','missing_fields':[]}
    if asks_history_proof(question):return {**action,'tool':'Fault_History_Query','query':question,'corpus':'synthetic','missing_fields':[]}
    kind=catalog_kind(question)
    if kind:return {**action,'tool':TOOLS.get(kind,'Hybrid_Search'),'query':question,'corpus':'synthetic','missing_fields':[]}
    subjectless=re.sub(r'(?:HIS|FM(?:ECA)?|MFL|FTA|RBD)-SYN-\d{3}','',question,flags=re.I)
    business=[k for k in topics(subjectless) if k in TOOLS]
    identifiers=re.findall(r'(?:HIS|FM(?:ECA)?|MFL|FTA|RBD)-SYN-\d{3}',question,re.I)
    if identifiers and not re.search(r'\d{8}',question):
        prefix=identifiers[0].split('-')[0].upper()
        target={'HIS':'Fault_History_Query','FM':'FMECA_Query','FMECA':'FMECA_Query','MFL':'MFL_PFL_Query','FTA':'FTA_Query','RBD':'RBD_Query'}[prefix]
        if len(business)==1 and TOOLS[business[0]]!=target and re.search(r'对应|关联|相关|维修清单|失效模式',question):
            return {**action,'tool':TOOLS[business[0]],'query':question,'corpus':'synthetic','missing_fields':[]}
        if len(set(x.split('-')[0].upper() for x in identifiers))==1:
            return {**action,'tool':target,'query':question,'corpus':'synthetic','missing_fields':[]}
    if business and re.search(r'区别|不同|比较|对比|是什么|什么是|什么意思|含义|定义|原理',question) and not re.search(r'\d{8}|FUNC-\d+',question,re.I):
        return {**action,'tool':'Hybrid_Search','query':question,'corpus':'synthetic','missing_fields':[]}
    if business and not re.search(r'论文|文献|学术|研究进展',question):
        action={**action,'corpus':'synthetic'}
        full_hmc=re.findall(r'(?<!\d)\d{8}[-－](?:\*\*|\d{2})[-－]\d{4}(?!\d)',question)
        if business==['hmc'] and full_hmc:
            # A complete code is present even when the database has no match.
            # Do not ask the user to supply the same identifier after not_found.
            query=question if len(set(full_hmc))==1 else action.get('query') or question
            if not re.search(r'\d{8}[-－](?:\*\*|\d{2})[-－]\d{4}',query):query=question
            return {**action,'tool':'HMC_Query','query':query,'missing_fields':[]}
        if business==['hmc'] and action['tool']=='Hybrid_Search':action={**action,'tool':'HMC_Query','query':question,'missing_fields':[]}
    return action
