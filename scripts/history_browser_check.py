"""Verify real history, deletion scope, typography and coverage explanations."""
from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import re
from zoneinfo import ZoneInfo

import httpx
from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[1]
BASE=os.getenv('ICP_APP_BASE_URL','http://127.0.0.1:8000')


def main():
    checks=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(channel='msedge',headless=True)
        context=browser.new_context(viewport={'width':1440,'height':1000},permissions=['clipboard-read','clipboard-write'])
        page=context.new_page();errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(BASE,wait_until='networkidle')
        expect(page.get_by_text('默认保留历史',exact=True)).to_be_visible()
        page.locator('.example-grid button').filter(has_text='查询故障代码').click()
        expect(page.locator('.answer-title span').last).to_have_text('已完成',timeout=90000)
        expect(page.locator('.history-item')).to_have_count(1)
        page.get_by_label('诊断问题').fill('计算RBD-SYN-001的10小时可靠度')
        page.get_by_role('button',name='发送问题',exact=True).click()
        expect(page.locator('.exchange')).to_have_count(2)
        expect(page.locator('.answer-text').last).to_contain_text('0.99951007',timeout=90000)
        expect(page.locator('.history-item')).to_have_count(1)
        expect(page.locator('.history-select small')).to_contain_text('2 次问答')
        checks.append({'name':'multiple_questions_grouped','passed':True})
        with page.expect_download() as exported:
            page.get_by_role('link',name='导出对话',exact=True).click()
        text=Path(exported.value.path()).read_text(encoding='utf-8')
        assert '91810001' in text and '0.99951007' in text and 'confirmation_token' not in text
        checks.append({'name':'conversation_export_without_credentials','passed':True})
        page.reload(wait_until='networkidle')
        expect(page.locator('.exchange')).to_have_count(2)
        expect(page.locator('.answer-text').last).to_contain_text('0.99951007')
        checks.append({'name':'reload_restores_full_history','passed':True})
        page.locator('.answer-actions').last.get_by_role('button',name='复制回答').click()
        expect(page.locator('.answer-actions').last.get_by_role('button',name='已复制')).to_be_visible()
        assert '0.99951007' in page.evaluate('navigator.clipboard.readText()')
        checks.append({'name':'copy_answer','passed':True})
        page.get_by_role('button',name='开始新诊断',exact=True).click()
        expect(page.locator('.exchange')).to_have_count(0)
        page.get_by_label('诊断问题').fill('查询PFL清单')
        page.get_by_role('button',name='发送问题',exact=True).click()
        expect(page.locator('.answer-title span').last).to_have_text('已完成',timeout=90000)
        expect(page.locator('.history-item')).to_have_count(2)
        page.locator('.history-select').filter(has_text='查询 HMC').click()
        expect(page.locator('.exchange')).to_have_count(2)
        checks.append({'name':'switch_between_conversations','passed':True})
        page.locator('.history-item.current').get_by_role('button',name=re.compile('重命名对话')).click()
        page.get_by_label('对话名称',exact=True).fill('历史恢复验收')
        page.get_by_role('button',name='保存名称',exact=True).click()
        expect(page.locator('.history-item.current strong')).to_have_text('历史恢复验收')
        page.get_by_label('搜索历史会话').fill('历史恢复')
        expect(page.locator('.history-item')).to_have_count(1)
        page.get_by_label('搜索历史会话').fill('')
        expect(page.locator('.history-item')).to_have_count(2)
        checks.append({'name':'rename_and_search_history','passed':True})
        page.get_by_role('button',name='清空历史会话',exact=True).click()
        expect(page.get_by_role('dialog',name='清空历史会话',exact=True)).to_be_visible()
        page.get_by_role('button',name='取消',exact=True).click()
        expect(page.locator('.history-item')).to_have_count(2)
        checks.append({'name':'clear_requires_confirmation','passed':True})
        page.locator('.history-item').filter(has_text='查询PFL清单').get_by_role('button',name=re.compile('删除对话')).click()
        page.get_by_role('button',name='确认删除对话',exact=True).click()
        expect(page.locator('.history-item')).to_have_count(1)
        expect(page.locator('.exchange')).to_have_count(2)
        checks.append({'name':'delete_one_keeps_other_conversation','passed':True})
        page.locator('.message-area').evaluate('e=>e.scrollTop=0')
        page.screenshot(path=str(ROOT/'outputs/screenshots/11-history-dialogue.png'))
        typography=page.locator('.answer-text').first.evaluate('e=>({size:parseFloat(getComputedStyle(e).fontSize),color:getComputedStyle(e).color,background:getComputedStyle(document.querySelector(".conversation")).backgroundColor})')
        def luminance(color):
            channels=[int(v)/255 for v in re.findall(r'\d+',color)[:3]]
            values=[v/12.92 if v<=.04045 else ((v+.055)/1.055)**2.4 for v in channels]
            return sum(v*w for v,w in zip(values,[.2126,.7152,.0722]))
        colors=sorted([luminance(typography['color']),luminance(typography['background'])])
        ratio=(colors[1]+.05)/(colors[0]+.05)
        assert typography['size']>=16 and ratio>=4.5
        checks.append({'name':'readable_answer_typography','passed':True,'font_pixels':typography['size'],'contrast':round(ratio,2)})
        page.get_by_role('button',name='知识库',exact=True).click()
        groups=page.locator('.issue-group')
        expect(groups).to_have_count(3)
        expect(groups.nth(0).locator('.issue-entry')).to_have_count(3)
        expect(groups.nth(1).locator('.issue-entry')).to_have_count(24)
        expect(groups.nth(2).locator('.issue-entry')).to_have_count(1)
        page.locator('.knowledge-issues').screenshot(path=str(ROOT/'outputs/screenshots/12-knowledge-coverage.png'))
        groups.nth(1).locator('summary').first.click()
        expect(groups.nth(1).get_by_role('link',name='查看原页').first).to_be_visible()
        assert '#page=' in groups.nth(1).get_by_role('link',name='查看原页').first.get_attribute('href')
        checks.append({'name':'coverage_grouping_and_source_links','passed':True})
        page.set_viewport_size({'width':390,'height':844})
        page.get_by_role('button',name='打开历史会话',exact=True).click()
        expect(page.locator('.history-panel')).to_be_visible()
        page.locator('.history-select').filter(has_text='历史恢复验收').click()
        expect(page.locator('.exchange')).to_have_count(2)
        assert page.evaluate('document.documentElement.scrollWidth<=390')
        checks.append({'name':'mobile_history_drawer','passed':True})
        page.set_viewport_size({'width':1440,'height':1000})
        page.get_by_role('button',name='清空历史会话',exact=True).click()
        page.get_by_role('button',name='确认清空历史',exact=True).click()
        expect(page.locator('.history-item')).to_have_count(0)
        expect(page.locator('.exchange')).to_have_count(0)
        checks.append({'name':'clear_own_history','passed':True})
        assert not errors
        checks.append({'name':'no_javascript_errors','passed':True})
        context.close();browser.close()
    report={'date':datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),'base_url':BASE,'mode':'real_platform_browser','checks':checks,'passed':all(c['passed'] for c in checks),'limitations':['会话历史是持久化浏览记录，不代表跨问题的模型对话记忆或公网多用户认证。']}
    (ROOT/'outputs/history-browser-check.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'passed':report['passed'],'checks':len(checks)}),flush=True)


if __name__=='__main__':main()
