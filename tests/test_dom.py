"""Offline DOM contract test; no network. Opt in with VISA_TEST_CHROMIUM path."""
import os
import unittest
import monitor

@unittest.skipUnless(os.environ.get("VISA_TEST_CHROMIUM"), "offline browser fixture is opt-in")
class DOMTests(unittest.TestCase):
    def test_only_public_section_cards(self):
        from playwright.sync_api import sync_playwright
        good = '<div class="flex-shrink-0"><div><span></span><span class="text-xs">武汉</span></div><span>B1/B2</span><span>2026-10-21</span><span><svg></svg>15小时</span></div>'
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, executable_path=None if os.environ["VISA_TEST_CHROMIUM"] == "bundled" else os.environ["VISA_TEST_CHROMIUM"])
            try:
                page = browser.new_page()
                page.route('**/*', lambda route: route.abort())
                page.set_content('<section><h2>最近抢位成功记录</h2>' + good * 3 + '</section><section><h2>Protected grid</h2>' + good.replace('武汉','北京') + '</section>')
                result = page.evaluate(monitor.EXTRACT_JS)
                self.assertEqual(len(result['records']), 3)
                self.assertEqual(result['malformed'], 0)
                self.assertEqual(len(monitor.validate(result)), 1)
                self.assertEqual(result['records'][0]['city'], '武汉')
                page.set_content('<section><h2>Protected grid</h2>' + good + '</section>')
                with self.assertRaises(monitor.MonitorError): monitor.validate(page.evaluate(monitor.EXTRACT_JS))
                page.set_content('<section><h2>最近抢位成功记录</h2>' + good.replace('2026-10-21','unknown') + '</section>')
                with self.assertRaises(monitor.MonitorError): monitor.validate(page.evaluate(monitor.EXTRACT_JS))
            finally:
                browser.close()
