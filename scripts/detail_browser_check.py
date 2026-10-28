"""Real records, independent confirmation choices and deliberate SSE interruption."""
from datetime import datetime
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo
from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[1]


def main():
    checks=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(channel='msedge',headless=True)
        context=browser.new_context(viewport={'width':1440,'height':1000})
        page=context.new_page();errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.goto(os.getenv('ICP_APP_BASE_URL','http://127.0.0.1:8000')+'/',wait_until='networkidle')
        page.get_by_role('button',name='知识库',exact=True).click()
        records=page.get_by_role('region',name='结构记录检索')
        expect(records.locator('.record-count')).to_have_text('共 100 条，匹配 100 条')
        expect(records.locator('.record-entry')).to_have_count(10)
        first=records.locator('.record-entry code').first.inner_text()
        records.get_by_role('button',name='结构记录下一页').click()
        assert records.locator('.record-entry code').first.inner_text()!=first
        checks.append({'name':'browse_actual_records_with_pagination','passed':True})
        records.get_by_label('搜索结构记录').fill('icu')
        expect(records.locator('.record-entry')).to_have_count(6)
        records.get_by_label('搜索结构记录').fill('91810001-**-0001')
        expect(records.locator('.record-entry')).to_have_count(1)
        records.locator('summary').click()
        expect(records.locator('dd')).to_contain_text(['91810001','**','0001','检查输入采样'])
        records.get_by_role('button',name='查看原始证据').click()
        expect(page.get_by_role('dialog',name='原始证据')).to_contain_text('输入过压钳位失效')
        page.get_by_role('button',name='关闭证据').click()
        page.locator('.record-browser').screenshot(path=str(ROOT/'outputs/screenshots/14-record-browser.png'))
        checks.append({'name':'search_code_alias_and_open_real_source','passed':True})
        records.get_by_label('搜索结构记录').fill('NO-SUCH-RECORD-XYZ')
        expect(records.get_by_text('没有找到匹配记录，请更换关键词或类别。')).to_be_visible()
        records.get_by_label('结构记录类别').select_option('mfl')
        expect(records.locator('.record-count')).to_have_text('共 72 条，匹配 72 条')
        checks.append({'name':'empty_result_and_category_reset','passed':True})
        page.get_by_role('button',name='诊断对话',exact=True).click()
        def ask(question):
            page.get_by_role('textbox',name='诊断问题').fill(question)
            page.get_by_role('button',name='发送问题',exact=True).click()
        ask('画面卡住但后台数据在更新，怎么排查？')
        expect(page.locator('.confirmation')).to_have_count(1,timeout=120000)
        ask('电源闪断一下系统就重启，怎么排查？')
        expect(page.locator('.confirmation')).to_have_count(2,timeout=120000)
        confirmations=page.locator('.confirmation')
        confirmations.first.locator('.candidate').first.click()
        expect(confirmations.first.get_by_role('button',name='确认功能并继续')).to_be_enabled()
        expect(confirmations.last.get_by_role('button',name='确认功能并继续')).to_be_disabled()
        confirmations.last.locator('.candidate').first.click()
        expect(confirmations.first.get_by_role('button',name='确认功能并继续')).to_be_disabled()
        expect(confirmations.last.get_by_role('button',name='确认功能并继续')).to_be_enabled()
        page.screenshot(path=str(ROOT/'outputs/screenshots/15-independent-confirmation.png'))
        checks.append({'name':'confirmation_choice_is_bound_to_its_task','passed':True})
        page.get_by_role('button',name='开始新诊断',exact=True).click()
        polls=[]
        page.on('request',lambda request:polls.append(request.url) if '/api/runs/' in request.url and '/events' not in request.url and request.method=='GET' else None)
        page.route('**/api/runs/*/events',lambda route:route.abort())
        ask('查询PFL清单')
        expect(page.locator('.answer-title span').last).to_have_text('已完成',timeout=120000)
        expect(page.locator('.answer-text').last).to_contain_text('未提供')
        assert len(polls)>=2
        expect(page.locator('.history-item.current')).not_to_contain_text('正在查询')
        page.screenshot(path=str(ROOT/'outputs/screenshots/16-stream-recovery.png'))
        checks.append({'name':'interrupted_sse_uses_repeated_real_snapshot_requests','passed':True,'snapshot_requests':len(polls)})
        page.unroute('**/api/runs/*/events')
        page.route('**/api/graphs/function/all',lambda route:route.abort())
        page.get_by_role('button',name='工程图谱',exact=True).click()
        page.get_by_role('button',name='系统功能与架构').click()
        expect(page.get_by_role('alert')).to_be_visible()
        checks.append({'name':'graph_network_failure_is_visible','passed':True})
        page.unroute('**/api/graphs/function/all')
        assert not errors
        checks.append({'name':'no_unhandled_javascript_errors','passed':True})
        context.close();browser.close()
    report={'date':datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),'base_url':os.getenv('ICP_APP_BASE_URL','http://127.0.0.1:8000'),'mode':'real_platform_browser_with_deliberate_sse_and_graph_transport_interruptions','checks':checks,'passed':all(c['passed'] for c in checks),'limitations':['仅模拟前端传输中断，不代表全部平台服务故障恢复。']}
    (ROOT/'outputs/detail-browser-check.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'passed':report['passed'],'checks':len(checks)}),flush=True)


if __name__=='__main__':main()
