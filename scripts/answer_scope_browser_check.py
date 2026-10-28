"""Exercise explanation selection, source labels, export and refresh in Edge."""
from datetime import datetime
import json
from pathlib import Path
from zoneinfo import ZoneInfo
from playwright.sync_api import expect,sync_playwright

ROOT=Path(__file__).resolve().parents[1]


def main():
    with sync_playwright() as p:
        browser=p.chromium.launch(channel='msedge',headless=True)
        page=browser.new_page(viewport={'width':1580,'height':1100})
        errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto('http://127.0.0.1:8000/',wait_until='networkidle')
        mode=page.get_by_role('combobox',name='回答方式')
        expect(mode).to_have_value('auto')
        def ask(question):
            page.get_by_role('textbox',name='诊断问题').fill(question)
            page.get_by_role('button',name='发送问题',exact=True).click()
            expect(page.locator('.user-question p').last).to_have_text(question,timeout=15000)
            expect(page.locator('.answer-title span').last).to_have_text('已完成',timeout=240000)
            return page.locator('.answer-text').last.inner_text()
        first=ask('航空icp是什么意思，一般的话会出现什么严重的问题')
        assert '通用知识说明' in first and '综合' in first and '处理' in first
        expect(page.get_by_text('本次使用模型常识',exact=True)).to_be_visible()
        assert page.locator('.citation-chips').count()==0
        page.screenshot(path=str(ROOT/'outputs/screenshots/20-general-icp-explanation.png'))
        mode.select_option('knowledge')
        second=ask('查询HMC 91810001-**-0001的含义和维修方法')
        assert '91810001-**-0001' in second
        expect(page.locator('.citation-chips').last).to_be_visible()
        page.screenshot(path=str(ROOT/'outputs/screenshots/21-knowledge-answer-mode.png'))
        mode.select_option('general')
        third=ask('什么是向量数据库，用生活中的例子解释一下')
        assert '通用知识说明' in third and '向量' in third
        expect(page.get_by_text('本次使用模型常识',exact=True)).to_be_visible()
        page.screenshot(path=str(ROOT/'outputs/screenshots/22-general-answer-mode.png'))
        with page.expect_download() as exported:page.get_by_role('link',name='导出对话',exact=True).click()
        text=Path(exported.value.path()).read_text(encoding='utf-8')
        assert all(answer in text for answer in [first,second,third])
        assert 'form_token' not in text
        page.reload(wait_until='networkidle')
        expect(page.locator('.answer-text').last).to_have_text(third)
        expect(page.get_by_text('本次使用模型常识',exact=True)).to_be_visible()
        assert not errors
        report={'date':datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),'passed':True,
            'checks':['default_auto','general_icp_explanation','no_false_source_chips','general_basis_label',
                      'knowledge_mode_real_code','knowledge_source_chips','explicit_general_mode','export_three_answers',
                      'export_no_private_form_fields','refresh_keeps_answer_and_basis','no_page_errors'],
            'screenshots':['outputs/screenshots/20-general-icp-explanation.png','outputs/screenshots/21-knowledge-answer-mode.png',
                           'outputs/screenshots/22-general-answer-mode.png']}
        (ROOT/'outputs/answer-scope-browser-check.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'passed':True,'checks':len(report['checks'])}),flush=True)
        browser.close()


if __name__=='__main__':main()
