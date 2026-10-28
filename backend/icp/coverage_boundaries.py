"""Known material gaps and the distinction between past cases and current evidence."""
import re


def unsupported_equipment_target(question):
    """A narrow explicit-subject guard, not a universal equipment classifier."""
    if re.search(r'FUNC-\d{3}|MOD-[A-Z-]+|\d{8}[-－](?:\*\*|\d{2})[-－]\d{4}',question,re.I):return None
    subject=re.sub(r'^(?:(?:请问|请|帮我|给我|能否|能不能|排查一下|诊断一下|排查|诊断|看看|定位|怎么处理|如何处理)[，,\s]*)+','',question.strip())
    target=re.match(r'(发动机|燃油泵|液压泵|起落架|轮胎|空调压缩机|APU)(?![A-Za-z])',subject,re.I)
    if target and re.search(r'故障|漏油|损坏|失效|排查|排故|不工作|报警|告警|异常|怎么办|咋办',question):return target[1]
    return None


def asks_equipment_requirements(question):
    if re.search(r'什么是|如何编写|怎么编写|概念|需求工程|需求管理',question):return False
    return bool(re.search(r'正式.{0,5}(?:系统|设备)?需求|设备.{0,5}系统需求|(?:查询|列出|查看|提供|有哪些).{0,4}系统需求|SystemRequirement|SYS[-‑]REQ',question,re.I))


def asks_history_proof(question):
    return (bool(re.search(r'历史|案例',question)) and bool(re.search(r'证明|确定|确认|当成|认定',question))
        and bool(re.search(r'本次|这次|此次|当前|现场|实际',question)) and bool(re.search(r'根因|原因',question)))


HISTORY_PROOF_POLICY=('历史案例只能提供候选原因和排查线索，不能直接证明本次故障根因。'
    '历史记录中的“已确认原因”仅属于那次案例。需要核对本次故障现象、发生阶段、完整HMC和现场检测结果，'
    '验证候选原因后才能认定本次根因；确认功能也不等于确认根因。当前问题未提供足以确认本次根因的现场证据。')
