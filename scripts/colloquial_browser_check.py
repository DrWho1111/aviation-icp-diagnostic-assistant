"""Verify the conversational regression in a real browser and exported dialogue."""
from datetime import datetime
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import expect,sync_playwright

ROOT=Path(__file__).resolve().parents[1]


def main():
    with sync_playwright() as p:
        browser=p.chromium.launch(channel='msedge',headless=True)
        page=browser.new_page(viewport={'width':1580,'height':1100},device_scale_factor=1)
        errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto('http://127.0.0.1:8000/',wait_until='networkidle')
        question='我们这个系统是干什么的，解释一下通道处理器是干什么'
        page.get_by_role('textbox',name='诊断问题').fill(question)
        page.get_by_role('button',name='发送问题',exact=True).click()
        expect(page.locator('.answer-title span').last).to_have_text('已完成',timeout=240000)
        answer=page.locator('.answer-text').last.inner_text()
        assert all(text in answer for text in ['关于本工作台','A','B','冗余'])
        assert not any(text in answer for text in ['暂不将相关段落拼成定义','第53 卷','CORPUS_VERSION='])
        with page.expect_download() as exported:
            page.get_by_role('link',name='导出对话',exact=True).click()
        export_text=Path(exported.value.path()).read_text(encoding='utf-8')
        assert question in export_text and answer in export_text
        assert not any(text in export_text for text in ['form_token','submitted_token'])
        page.reload(wait_until='networkidle')
        expect(page.locator('.answer-title span').last).to_have_text('已完成')
        expect(page.locator('.answer-text').last).to_have_text(answer)
        page.locator('.message-area').evaluate('e=>e.scrollTop=0')
        screenshot=ROOT/'outputs/screenshots/19-colloquial-compound-answer.png'
        page.screenshot(path=str(screenshot))
        page.locator('.citation-chips button').first.click()
        expect(page.get_by_role('dialog',name='原始证据')).to_be_visible()
        assert not errors
        report={'date':datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),'passed':True,'question':question,
                'answer':answer,'checks':['compound_parts_answered','no_raw_excerpt_dump','final_completed_status',
                'dialogue_export_matches_final','no_private_form_fields_in_export','reload_keeps_final_answer','source_dialog','no_page_errors'],
                'screenshot':screenshot.relative_to(ROOT).as_posix()}
        (ROOT/'outputs/colloquial-browser-check.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'passed':True,'checks':len(report['checks'])}),flush=True)
        browser.close()


if __name__=='__main__':main()
