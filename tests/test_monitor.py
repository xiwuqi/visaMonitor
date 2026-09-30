import copy
import datetime as dt
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import monitor as m

TODAY = dt.date(2026, 9, 30)
CUTOFF = dt.date(2026, 11, 14)

def card(date="2026-10-21", city="武汉", visa="B1/B2", age="15小时"):
    return dict(city=city, visa=visa, date=date, age=age)

def data(*records):
    return {"records": list(records), "malformed": 0}

class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "state.json"
        self.pushes = []

    def run_mode(self, rows, mode="monitor", sender=None):
        return m.process(rows, mode, self.path, ("武汉",), "B1/B2", CUTOFF, TODAY, sender=sender or (lambda *args: self.pushes.append(args)))

    def baseline(self, *rows):
        self.run_mode(data(*(rows or [card()])), "baseline")

    def test_baseline_suppresses_backlog_and_duplicates(self):
        self.baseline(card(), card(age="14小时"))
        self.assertEqual(len(m.load_state(self.path)["seen"]), 1)
        self.assertEqual(self.pushes, [])
        self.run_mode(data(card(age="2天")))
        self.assertEqual(self.pushes, [])

    def test_disappear_reappear_remains_seen(self):
        self.baseline()
        self.run_mode(data(card(city="北京")))
        self.run_mode(data(card(age="3天")))
        self.assertEqual(self.pushes, [])

    def test_dates_city_type_exact_and_cutoff_inclusive(self):
        self.baseline(card(city="北京"))
        self.run_mode(data(card("2026-11-14"), card("2026-11-15"), card("2026-09-29"), card(visa="B1"), card(city="上海")))
        self.assertEqual(len(self.pushes), 1)
        body = self.pushes[0][1]
        self.assertIn("2026-11-14", body)
        for excluded in ("2026-11-15", "2026-09-29", "上海"):
            self.assertNotIn(excluded, body)
        self.assertIn(m.LABEL, body)
        self.assertIn("子类别未知", body)

    def test_missing_state_fails_no_baseline(self):
        with self.assertRaisesRegex(m.MonitorError, "STATE_MISSING"):
            self.run_mode(data(card()))
        self.assertFalse(self.path.exists())

    def test_corrupt_state_fails(self):
        for content in ('garbage', '{}', '{"schema":2,"initialized_at":"x","seen":["garbage"]}'):
            self.path.write_text(content)
            with self.assertRaisesRegex(m.MonitorError, "STATE_INVALID"):
                self.run_mode(data(card()))

    def test_bad_source_fails_in_all_modes(self):
        for bad in ({}, data(), {"records": [card()], "malformed": 1}, data(card("2026-02-30")), data(card(age=""))):
            for mode in ("baseline", "status", "monitor"):
                with self.assertRaisesRegex(m.MonitorError, "PARSE_FAILED"):
                    self.run_mode(bad, mode)
        self.assertEqual(self.pushes, [])

    def test_failed_push_keeps_state_and_can_retry(self):
        self.baseline()
        previous = self.path.read_bytes()
        def fail(*_): raise m.MonitorError("PUSH_FAILED")
        with self.assertRaises(m.MonitorError):
            self.run_mode(data(card("2026-11-04")), sender=fail)
        self.assertEqual(previous, self.path.read_bytes())
        self.run_mode(data(card("2026-11-04")))
        self.assertEqual(len(self.pushes), 1)

    def test_status_is_snapshot_does_not_change_state(self):
        self.baseline()
        before = self.path.read_bytes()
        self.run_mode(data(card("2026-11-04")), "status")
        self.assertEqual(before, self.path.read_bytes())
        self.assertIn("非新增提醒", self.pushes[0][0])
        self.run_mode(data(card("2026-11-04")))
        self.assertEqual(len(self.pushes), 2)

    def test_empty_match_snapshot_does_not_claim_no_slots(self):
        self.run_mode(data(card(city="上海")), "status")
        self.assertIn("不代表没有余位", self.pushes[0][1])

    def test_rebaseline_refused(self):
        self.baseline()
        with self.assertRaisesRegex(m.MonitorError, "BASELINE_REFUSED"):
            self.baseline()

    def test_main_fetch_error_is_nonzero(self):
        with patch.dict(os.environ, {"VISA_CUTOFF": "2026-11-14", "VISA_CITIES": "武汉", "VISA_TYPE": "B1/B2"}, clear=True), patch.object(m, "scrape", side_effect=m.MonitorError("FETCH_FAILED")):
            self.assertEqual(m.main(["--check"]), 1)

    def test_main_missing_topic_is_nonzero(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(m.main(["--test-push"]), 1)

    def test_main_parse_error_is_nonzero(self):
        with patch.dict(os.environ, {"VISA_CUTOFF": "2026-11-14", "VISA_CITIES": "武汉", "VISA_TYPE": "B1/B2"}, clear=True), patch.object(m, "scrape", return_value=data()):
            self.assertEqual(m.main(["--check"]), 1)

    def test_filters_and_required_cutoff_validated(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(m.MonitorError): m.config()
        with patch.dict(os.environ, {"VISA_CUTOFF": "2026-11-14", "VISA_DESC": "All Others"}, clear=True):
            with self.assertRaises(m.MonitorError): m.config()

    def test_large_message_bounded_with_disclaimer_and_omission(self):
        records = [card(city="武汉" + str(i)) for i in range(200)]
        title, body = m.message(records)
        self.assertLessEqual(len(body.encode("utf-8")), 3500)
        self.assertIn(m.LABEL, body)
        self.assertIn("子类别未知", body)
        self.assertIn("未展开", body)

    def test_expired_keys_pruned(self):
        self.baseline(card("2026-09-29"), card("2026-10-21"))
        state = m.load_state(self.path)
        self.assertEqual(len(state["seen"]), 1)
        self.assertIn("2026-10-21", state["seen"][0])

    def test_json_push_unicode_and_ack(self):
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def read(self): return b'{"event":"message","id":"test-id"}'
        with patch.dict(os.environ, {"VISA_NTFY_TOPIC": "fake-unit-test-topic"}, clear=True), patch.object(m.urllib.request, "urlopen", return_value=Response()) as opener:
            m.push(m.LABEL, "测试")
            request = opener.call_args.args[0]
            payload = json.loads(request.data)
            self.assertEqual(payload["title"], m.LABEL)
            self.assertEqual(payload["topic"], "fake-unit-test-topic")

    def test_push_bad_ack_fails(self):
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def read(self): return b'{}'
        with patch.dict(os.environ, {"VISA_NTFY_TOPIC": "fake-unit-test-topic"}, clear=True), patch.object(m.urllib.request, "urlopen", return_value=Response()):
            with self.assertRaisesRegex(m.MonitorError, "PUSH_FAILED"):
                m.push("test", "test")

if __name__ == "__main__":
    unittest.main()
