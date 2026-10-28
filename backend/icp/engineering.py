"""Deterministic engineering calculations, independent of language models."""
from __future__ import annotations

import itertools
import math
import re


def reliability(expression: str, rates: dict[str, float], hours: float) -> float:
    if not math.isfinite(hours) or hours < 0 or hours > 1e7:
        raise ValueError("任务时间必须为0至10000000之间的有限小时数")
    tokens = re.findall(r"[A-Z][A-Z0-9-]*|\d+|[(),]", expression)
    if "".join(tokens) != re.sub(r"\s+", "", expression):
        raise ValueError("不支持的可靠性表达式")
    pos = 0

    def parse() -> float:
        nonlocal pos
        if pos >= len(tokens):
            raise ValueError("表达式不完整")
        token = tokens[pos]
        pos += 1
        if token not in {"SERIES", "PARALLEL", "KOFN"}:
            if token not in rates or not math.isfinite(rates[token]) or rates[token] < 0:
                raise ValueError(f"缺少或非法的模块失效率：{token}")
            return math.exp(-rates[token] * hours)
        if pos >= len(tokens) or tokens[pos] != "(":
            raise ValueError("缺少左括号")
        pos += 1
        k = None
        if token == "KOFN":
            k = int(tokens[pos])
            pos += 1
            if tokens[pos] != ",":
                raise ValueError("表决门参数错误")
            pos += 1
        children = [parse()]
        while pos < len(tokens) and tokens[pos] == ",":
            pos += 1
            children.append(parse())
        if pos >= len(tokens) or tokens[pos] != ")":
            raise ValueError("缺少右括号")
        pos += 1
        if token == "SERIES":
            return math.prod(children)
        if token == "PARALLEL":
            return 1 - math.prod(1 - p for p in children)
        if not 1 <= k <= len(children):
            raise ValueError("表决门k超出范围")
        # Poisson-binomial dynamic programming; supports non-identical units.
        probabilities = [1.0] + [0.0] * len(children)
        for p in children:
            probabilities = [probabilities[j] * (1-p) + (probabilities[j-1] * p if j else 0)
                             for j in range(len(probabilities))]
        return sum(probabilities[k:])

    result = parse()
    if pos != len(tokens):
        raise ValueError("表达式包含多余内容")
    return result


def fault_tree_probability(probabilities: dict[str, float], cuts: list[list[str]]) -> float:
    """Enumerate UNIQUE event states. Overlapping cuts never imply independent branches."""
    if len(probabilities) > 18:
        raise ValueError("本原型仅支持18个以内独立底事件的精确枚举")
    if any(not math.isfinite(p) or not 0 <= p <= 1 for p in probabilities.values()):
        raise ValueError("底事件概率必须为0至1之间的有限数值")
    if any(not cut or any(e not in probabilities for e in cut) for cut in cuts):
        raise ValueError("割集包含不存在的事件或空割集")
    events = list(probabilities)
    total = 0.0
    for states in itertools.product((False, True), repeat=len(events)):
        active = {e for e, state in zip(events, states) if state}
        if any(set(cut) <= active for cut in cuts):
            total += math.prod(probabilities[e] if state else 1-probabilities[e]
                               for e, state in zip(events, states))
    return total


def minimal_cuts(cuts: list[list[str]]) -> list[list[str]]:
    unique = {frozenset(cut) for cut in cuts}
    return [sorted(cut) for cut in sorted(unique, key=lambda x: (len(x), sorted(x)))
            if not any(other < cut for other in unique)]


def cuts_from_graph(graph: dict) -> list[list[str]]:
    nodes={n["id"]:n for n in graph["nodes"]}
    inputs={nid:[] for nid in nodes}
    for edge in graph["edges"]:
        if edge["type"] in {"gate_input","gate_output"}:
            inputs[edge["target"]].append(edge["source"])
    def visit(nid,path):
        if nid in path: raise ValueError("故障树包含循环")
        node=nodes[nid]
        if node["type"]=="FTABasicEvent": return [[nid]]
        children=inputs[nid]
        if not children: raise ValueError("门或事件缺少输入")
        values=[visit(child,path|{nid}) for child in children]
        if node["type"]=="FTAGate" and node["properties"]["gate_kind"]=="AND":
            return minimal_cuts([list(set().union(*[set(cut) for cut in combination])) for combination in itertools.product(*values)])
        return minimal_cuts([cut for group in values for cut in group])
    return visit(graph["id"],set())

