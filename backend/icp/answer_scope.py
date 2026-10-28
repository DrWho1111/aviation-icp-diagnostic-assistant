"""Choose model background explanation separately from equipment evidence."""
import re

from .coverage_boundaries import asks_equipment_requirements,asks_history_proof,unsupported_equipment_target
from .query_intent import catalog_kind,workspace_question
from .retrieval_boundaries import definition_term,module_subject_records


def general_explanation(question,kb,mode='auto'):
    if mode=='knowledge':return False
    # These requests need source records, exact parameters or incident evidence,
    # even when the user selects general discussion.
    if re.search(r'(?<!\d)\d{8}(?!\d)|(?:FUNC|MOD|FM|FMECA|MFL|HIS|FTA|RBD)-|SYN-ICP|CONFIG-|' 
                 r'计算|最小割集|PFL|故障率|失效率|概率(?:是|为|多少)|可靠度(?:是|为|多少)|'
                 r'本次|这次|此次|当前(?:故障|状态)|现场|我的(?:设备|系统)|这台|这个型号|'
                 r'不存在|虚构|编造|猜测|忽略.{0,8}(?:规则|指令|限制)',question,re.I):return False
    if asks_equipment_requirements(question) or asks_history_proof(question) or unsupported_equipment_target(question):return False
    if re.search(r'排查|排故|怎么修|怎么处理|如何处理|怎么办|咋办|咋整|怎么查|如何查|咋查',question):return False
    if re.search(r'(?:根据|依据|按照|引用|查阅|查找|检索|只用|仅用).{0,12}(?:资料|文档|论文|知识库|原文)|'
                 r'来源|出处|证据|页码|第.{0,6}[页章表]|研究资料|资料包|手册|正式需求',question):return False
    application_help,_=workspace_question(question)
    if application_help:return False
    if catalog_kind(question):return False
    if mode=='general':return True
    term=definition_term(question)
    if module_subject_records(kb,term)[0]:return False
    # Actual named modules stay in their equipment context (ICU, for example,
    # must not become a medical ICU just because the question is short).
    if any(re.search(r'(?<![A-Za-z0-9-])'+re.escape(m['fields']['alias'])+r'(?![A-Za-z0-9-])',question,re.I)
           or m['name'] in question for m in kb.by_kind['module']):return False
    # Domain concepts default to evidence-based answering. A request for a
    # general explanation (or explicit general mode) can still use the model.
    if (re.search(r'(?<![A-Za-z])(?:ICP|RBD|FTA|FMECA|HMC|MFL)(?![A-Za-z])|综合(?:核心)?处理机|系统工程|可靠性框图|故障树|功能架构',question,re.I)
        and not re.search(r'一般|通常|常见|科普|通俗|通用知识|常识|生活.{0,4}例子|不用查资料|不必引用',question)):
        return False
    return bool(term or re.search(r'科普|通俗解释|打个比方|生活例子|一般.{0,12}(?:问题|风险|故障)|'
                                  r'通常.{0,12}(?:问题|风险|故障)|常见.{0,8}(?:问题|风险|故障)',question))
