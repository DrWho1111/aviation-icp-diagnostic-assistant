"""Exercise real pages and preserve screenshots; no mocked network responses."""
from __future__ import annotations

import json
import os
from pathlib import Path
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"outputs/screenshots"


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    checks=[]
    with sync_playwright() as p:
        # Use an installed browser when available; never require a particular user's cache path.
        try:
            browser=p.chromium.launch(channel="msedge",headless=True)
        except Exception:
            browser=p.chromium.launch(headless=True)
        page=browser.new_page(viewport={"width":1440,"height":1000},device_scale_factor=1)
        page_errors=[]
        page.on("pageerror",lambda error:page_errors.append(str(error)))
        def capture(name):
            page.screenshot(path=str(OUT/(name+".png")),full_page=False)
        page.goto(os.getenv('ICP_APP_BASE_URL','http://127.0.0.1:8000'),wait_until="networkidle")
        expect(page.get_by_role("heading",name="用现象提问，用证据诊断")).to_be_visible()
        capture("01-home")
        checks.append({"name":"home","passed":True})
        page.locator(".example-grid button").filter(has_text="从异常现象开始").click()
        expect(page.get_by_text("选择与当前现象最相符的功能",exact=True)).to_be_visible(timeout=90000)
        expect(page.locator(".candidate")).to_have_count(3)
        page.reload(wait_until='networkidle')
        expect(page.locator('.candidate')).to_have_count(3)
        checks.append({'name':'pending_confirmation_restored_after_reload','passed':True})
        capture("02-confirm-function")
        page.locator(".candidate").filter(has_text="显示画面合成").click()
        page.get_by_role("button",name="确认功能并继续").click()
        expect(page.locator(".answer-title span").last).to_have_text("已完成",timeout=90000)
        expect(page.locator(".answer-text").last).to_contain_text("已确认功能：显示画面合成")
        expect(page.locator(".answer-text").last).to_contain_text("尚未确认本次根因")
        page.locator(".message-area").evaluate("e=>e.scrollTop=0")
        capture("03-diagnostic-result")
        checks.append({"name":"diagnosis_requires_confirmation","passed":True})
        page.locator(".trace summary").last.click()
        with page.expect_download() as exported:
            page.locator(".trace-export").last.click()
        trace_text=Path(exported.value.path()).read_text(encoding="utf-8")
        assert "Thought：" in trace_text and "Action：Fault_Reasoning_Engine[" in trace_text
        assert "Observation：" in trace_text and '"branches"' in trace_text
        assert all(len(block.splitlines())==3 for block in trace_text.split("\n\n"))
        checks.append({"name":"three_line_trace_download","passed":True})
        page.locator(".citation-chips button").first.click()
        expect(page.get_by_role("dialog",name="原始证据")).to_be_visible()
        capture("04-source-evidence")
        page.get_by_role("button",name="关闭证据").click()
        checks.append({"name":"evidence_dialog","passed":True})
        page.get_by_role("button",name="开始新诊断").click()
        page.locator(".example-grid button").filter(has_text="查询故障代码").click()
        expect(page.locator(".answer-title span").last).to_have_text("已完成",timeout=90000)
        expect(page.locator(".answer-text").last).to_contain_text("91810001-**-0001")
        capture("05-hmc-query")
        checks.append({"name":"hmc_exact_query","passed":True})
        page.get_by_role("button",name="开始新诊断").click()
        page.locator(".example-grid button").filter(has_text="计算任务可靠度").click()
        expect(page.locator(".answer-text").last).to_contain_text("0.99951007",timeout=90000)
        capture("06-rbd-calculation")
        checks.append({"name":"rbd_value","passed":True})
        page.get_by_role("button",name="知识库",exact=True).click()
        expect(page.get_by_role("heading",name="资料清单与处理状态")).to_be_visible()
        expect(page.locator("tbody tr")).to_have_count(31)
        capture("07-knowledge-inventory")
        checks.append({"name":"inventory_31_documents","passed":True})
        page.get_by_role("button",name="工程图谱",exact=True).click()
        page.locator(".graph-menu button").filter(has_text="FTA-SYN-004").click()
        expect(page.locator(".graph-view img")).to_be_visible()
        page.locator(".graph-view img").evaluate("e=>e.decode()")
        capture("08-shared-event-fta")
        checks.append({"name":"source_graph_preview","passed":True})
        page.get_by_role("button",name="评测记录",exact=True).click()
        expect(page.locator("tbody tr")).to_have_count(40)
        capture("09-core-evaluation")
        checks.append({"name":"evaluation_40_cases","passed":True})
        page.get_by_role("button",name="开始新诊断").click()
        page.set_viewport_size({"width":390,"height":844})
        expect(page.get_by_label("诊断问题")).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth")<=390
        capture("10-mobile-home")
        checks.append({"name":"mobile_no_horizontal_overflow","passed":True})
        assert not page_errors, "浏览器产生未捕获的脚本错误"
        checks.append({"name":"no_javascript_errors","passed":True})
        browser.close()
    health=httpx.get(os.getenv('ICP_APP_BASE_URL','http://127.0.0.1:8000')+'/api/health',trust_env=False).raise_for_status().json()
    output={"mode":health['mode'],"date":datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),"base_url":os.getenv('ICP_APP_BASE_URL','http://127.0.0.1:8000'),"corpus_version":health['corpus_version'],"checks":checks,"passed":all(c["passed"] for c in checks),"screenshots":[f.name for f in sorted(OUT.glob("*.png"))]}
    (ROOT/"outputs/browser-check.json").write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"passed":output["passed"],"checks":len(checks),"screenshots":len(output["screenshots"])},ensure_ascii=False))


if __name__=="__main__":
    main()
