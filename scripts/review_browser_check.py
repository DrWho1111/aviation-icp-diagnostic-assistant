"""Verify reference continuation and source-page review in the real browser."""
from datetime import datetime
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo
from playwright.sync_api import sync_playwright,expect

ROOT=Path(__file__).resolve().parents[1]


def main():
    checks=[]
    with sync_playwright() as w:
        browser=w.chromium.launch(channel='msedge',headless=True)
        page=browser.new_page(viewport={'width':1440,'height':1000});errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.goto(os.getenv('ICP_APP_BASE_URL','http://127.0.0.1:8000')+'/',wait_until='networkidle')
        def ask(text,status='已完成'):
            page.get_by_label('诊断问题',exact=True).fill(text)
            page.get_by_label('诊断问题',exact=True).press('Enter')
            expect(page.locator('.answer-title span').last).to_have_text(status,timeout=180000)
        ask('计算RBD-SYN-001可靠度','需要补充信息')
        checks.append({'name':'missing_time_pauses','passed':True})
        ask('那10小时呢')
        expect(page.locator('.answer-text').last).to_contain_text('0.99951007')
        expect(page.locator('.context-note').last).to_contain_text('RBD-SYN-001')
        checks.append({'name':'explicit_time_followup_and_visible_context','passed':True})
        page.screenshot(path=str(ROOT/'outputs/screenshots/17-context-followup.png'))
        page.reload(wait_until='networkidle')
        expect(page.locator('.answer-text').last).to_contain_text('0.99951007')
        expect(page.locator('.context-note').last).to_be_visible()
        checks.append({'name':'resolved_context_survives_reload','passed':True})
        ask('那-10小时呢','需要补充信息')
        expect(page.locator('.answer-text').last).to_contain_text('非负')
        checks.append({'name':'negative_followup_is_not_positive_time','passed':True})
        page.get_by_role('button',name='开始新诊断').click()
        ask('电源部分有哪些MFL')
        expect(page.locator('.answer-text').last).to_contain_text('15')
        assert 'MFL故障码' not in page.locator('.answer-text').last.inner_text()
        page.screenshot(path=str(ROOT/'outputs/screenshots/18-scoped-catalog.png'))
        checks.append({'name':'scoped_catalog_keeps_count_and_terminology','passed':True})
        page.get_by_role('button',name='知识库',exact=True).click()
        group=page.locator('.issue-group').nth(1);group.locator('summary').first.click()
        expect(group.locator('.issue-entry').filter(has_text='OCR已校正并纳入当前知识文本')).to_have_count(14)
        expect(group.locator('.issue-entry').filter(has_text='已核对：空白页')).to_have_count(10)
        checks.append({'name':'review_notes_match_fourteen_admitted_pages_and_ten_blank_pages','passed':True})
        assert not errors;checks.append({'name':'no_javascript_errors','passed':True})
        browser.close()
    report={'date':datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),'base_url':os.getenv('ICP_APP_BASE_URL','http://127.0.0.1:8000'),'checks':checks,'passed':all(c['passed'] for c in checks)}
    (ROOT/'outputs/review-browser-check.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'passed':report['passed'],'checks':len(checks)}))


if __name__=='__main__':main()
