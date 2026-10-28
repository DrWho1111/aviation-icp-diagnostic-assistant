"""Verify a free-form catalogue question through the real browser UI."""
import json
import os
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='msedge', headless=True)
        page = browser.new_page(viewport={'width': 1580, 'height': 1100}, device_scale_factor=1)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(os.getenv('ICP_APP_BASE_URL','http://127.0.0.1:8000')+'/', wait_until='networkidle')
        page.get_by_role('textbox', name='诊断问题').fill('hmc都有什么呢')
        page.get_by_role('button', name='发送问题', exact=True).click()
        expect(page.locator('.answer-title span').last).to_have_text('已完成', timeout=180000)
        answer = page.locator('.answer-text').last.inner_text()
        assert 'HMC' in answer and '电源' in answer and '30' in answer
        expect(page.locator('.coverage-grid').get_by_text('100', exact=True)).to_be_visible()
        assert not any(s in answer for s in ['CORPUS_VERSION=', 'RECORD_ID=', '\\n', '硬件管理代码'])
        expect(page.locator('.citation-chips button').first).to_be_visible()
        page.locator('.message-area').evaluate('e=>e.scrollTop=0')
        image = ROOT / 'outputs/screenshots/13-open-question.png'
        page.screenshot(path=str(image))
        page.locator('.citation-chips button').first.click()
        expect(page.get_by_role('dialog', name='原始证据')).to_contain_text('HMC故障代码手册')
        assert not errors
        report = {'passed': True, 'question': 'hmc都有什么呢', 'answer': answer,
                  'checks': ['natural_answer', 'correct_count', 'no_index_dump', 'source_dialog', 'no_page_errors'],
                  'screenshot': image.relative_to(ROOT).as_posix()}
        (ROOT / 'outputs/open-question-browser-check.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'passed': True, 'checks': len(report['checks'])}))
        browser.close()


if __name__ == '__main__':
    main()
