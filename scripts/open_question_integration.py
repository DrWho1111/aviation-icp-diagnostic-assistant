"""Real open-question checks against the running Dify/Qwen application."""
import json
from pathlib import Path
import time
import uuid

import httpx
from model_integration import wait, snapshot

ROOT = Path(__file__).resolve().parents[1]
CASES = [
    ('original', 'hmc都有什么呢', 'HMC_Query', 'hmc', 100, 100),
    ('definition', '能先讲讲健康管理码吗，我还不知道具体编号', 'HMC_Query', 'hmc', 100, 100),
    ('filtered', '电源部分有哪些HMC？', 'HMC_Query', 'hmc', 100, 30),
    ('count', '故障代码一共多少条', 'HMC_Query', 'hmc', 100, 100),
    ('subsystems', '给我列一下系统的全部子系统', 'Hybrid_Search', 'subsystem', 6, 6),
    ('mfl', 'MFL都包含哪些内容', 'MFL_PFL_Query', 'mfl', 72, 72),
    ('fta', '列出全部FTA模型', 'FTA_Query', 'fta', 8, 8),
    ('comparison', 'HMC和FTA有什么区别', 'Hybrid_Search', None, None, None),
    ('absent', '发动机有哪些HMC', 'HMC_Query', None, None, None),
    ('general', '用一句话解释什么是光合作用', None, None, None, None),
]


def main():
    report = {'mode': 'real_dify_qwen_open_questions', 'cases': [],
              'limitations': ['十种表达的功能检查，不代表开放问题总体准确率']}
    output = ROOT / 'outputs/open-question-integration.json'
    with httpx.Client(base_url='http://127.0.0.1:8000', trust_env=False, timeout=20) as client:
        deadline = time.monotonic() + 60
        while True:
            try:
                report['corpus_version'] = client.get('/api/health').raise_for_status().json()['corpus_version']
                break
            except httpx.HTTPError:
                if time.monotonic() >= deadline: raise RuntimeError('Application health did not become ready') from None
                time.sleep(.5)
        for cid, question, tool, kind, total, matched in CASES:
            start = time.monotonic()
            item = {'id': cid, 'question': question, 'passed': False}
            try:
                response = client.post('/api/chat', json={'question': question, 'session_id': 'open-check-' + uuid.uuid4().hex}).raise_for_status().json()
                run = wait(client, response['run_id'])
                item['run'] = snapshot(run)
                answer = run.get('answer', '')
                traces = run.get('trace', [])
                checks = {'completed': run['status'] == 'completed', 'readable': bool(answer) and all(s not in answer for s in ['CORPUS_VERSION=', 'RECORD_ID=', 'SOURCE_ID=', '\\n', '<think>', '{"content"'])}
                if tool:
                    checks['tool'] = any(t['tool'] == tool and t.get('backend') == 'dify' for t in traces)
                    checks['planner'] = run.get('planner_mode') == 'dify'
                if kind:
                    coverage = next((t.get('observation', {}).get('coverage', {}) for t in traces if t['tool'] == tool), {})
                    checks['catalog'] = coverage.get('view') == 'catalog' and coverage.get('kind') == kind
                    checks['total'] = coverage.get('total') == total
                    checks['matched'] = coverage.get('matched') == matched
                    checks['business_sources'] = bool(run['evidence']) and all(e['corpus'] == 'synthetic' for e in run['evidence'])
                if cid == 'count': checks['answer_count'] = '100' in answer
                if cid in {'original','definition'}: checks['natural_generation'] = run.get('generation') == 'llm'
                if 'HMC' in question.upper() or '健康管理码' in question or '故障代码' in question:
                    checks['no_invented_expansion'] = all(s not in answer for s in ['硬件管理代码', '硬件维护手册'])
                if cid == 'comparison':
                    checks['comparison'] = any(t.get('observation', {}).get('coverage', {}).get('view') == 'comparison' for t in traces)
                    checks['grounded'] = run.get('generation') == 'llm' and bool(run['evidence'])
                if cid == 'absent': checks['honest_absence'] = any(t['status'] == 'not_found' for t in traces) and '未找到' in answer
                if cid == 'general': checks['general_llm'] = run.get('generation') == 'llm' and '光' in answer and '植物' in answer
                item.update(checks=checks, passed=all(checks.values()))
            except (httpx.HTTPError, TimeoutError, KeyError, ValueError) as exc:
                item['error'] = type(exc).__name__
            item['seconds'] = round(time.monotonic() - start, 2)
            report['cases'].append(item)
            report['summary'] = {'total': len(CASES), 'executed': len(report['cases']), 'passed': sum(c['passed'] for c in report['cases'])}
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps({k: item[k] for k in ['id', 'passed', 'seconds']}), flush=True)
    return all(c['passed'] for c in report['cases'])


if __name__ == '__main__':
    raise SystemExit(not main())
