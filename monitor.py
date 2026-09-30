#!/usr/bin/env python3
"""Observe qmq.app's public historical-success ticker, never live availability."""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import re
import sys
import urllib.request
from zoneinfo import ZoneInfo

URL = "https://qmq.app"
LABEL = "历史成功记录，非实时余位"
SCHEMA = 2

# Public four-field ticker cards only. No clicks, protected grid, tokens or
# browser identity changes. Fail closed when the known card shape changes.
EXTRACT_JS = r"""() => {
  const heading = Array.from(document.querySelectorAll('h2')).find(
    h => h.textContent.trim() === '最近抢位成功记录');
  const section = heading && heading.closest('section');
  if (!section) return {error: 'historical-section-missing'};
  const records = [];
  let malformed = 0;
  for (const card of section.querySelectorAll('div.flex-shrink-0')) {
    const kids = Array.from(card.children);
    if (kids.length !== 4) { malformed++; continue; }
    const date = kids[2].textContent.trim();
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) { malformed++; continue; }
    const city = kids[0].textContent.trim();
    const visa = kids[1].textContent.trim();
    const age = kids[3].textContent.trim();
    if (!city || !visa || !age) { malformed++; continue; }
    records.push({city, visa, date, age});
  }
  return {records, malformed};
}"""


class MonitorError(RuntimeError):
    pass


def env(name, default=""):
    return os.environ.get("VISA_" + name) or default


def config():
    cities = tuple(c.strip() for c in env("CITIES").split(",") if c.strip())
    visa = env("TYPE").strip()
    if not cities or not visa:
        raise MonitorError("CONFIG_FAILED: cities and visa type are required")
    try:
        cutoff = dt.date.fromisoformat(env("CUTOFF"))
    except ValueError:
        raise MonitorError("CONFIG_FAILED: VISA_CUTOFF must be an ISO date") from None
    # Historical cards have no subtype. Refuse old subtype filters rather than
    # silently claiming that the visible record is a regular interview.
    if env("DESC") or env("DESC_BY_CITY"):
        raise MonitorError("CONFIG_FAILED: historical cards cannot support subtype filters")
    return cities, visa, cutoff


def today():
    return dt.datetime.now(ZoneInfo("Asia/Shanghai")).date()


def scrape():
    from playwright.sync_api import sync_playwright
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page(locale="zh-CN")
                response = page.goto(URL, wait_until="domcontentloaded", timeout=60_000)
                if response is None or not response.ok:
                    raise MonitorError("FETCH_FAILED: public page HTTP response unsuccessful")
                page.wait_for_function(
                    "() => Array.from(document.querySelectorAll('h2')).some(h => "
                    "h.textContent.trim() === '最近抢位成功记录' && "
                    "h.closest('section')?.querySelector('div.flex-shrink-0'))",
                    timeout=45_000,
                )
                return page.evaluate(EXTRACT_JS)
            finally:
                browser.close()
    except MonitorError:
        raise
    except Exception as exc:
        # Do not leak browser/network URLs or secrets into public Actions logs.
        raise MonitorError(f"FETCH_FAILED: {type(exc).__name__}; public historical ticker unreadable") from None


def validate(data):
    if not isinstance(data, dict) or data.get("malformed") or not isinstance(data.get("records"), list) or not data["records"]:
        raise MonitorError("PARSE_FAILED: missing, empty or malformed public historical ticker")
    records = {}
    for r in data["records"]:
        if not isinstance(r, dict) or any(not isinstance(r.get(k), str) or not r[k].strip() for k in ("city", "visa", "date", "age")):
            raise MonitorError("PARSE_FAILED: malformed historical card")
        try:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", r["date"]):
                raise ValueError
            dt.date.fromisoformat(r["date"])
        except ValueError:
            raise MonitorError("PARSE_FAILED: invalid appointment date") from None
        # Age deliberately not in key: a card changing from 3小时 to 4小时
        # must not become a new alert. Duplicated marquee cards collapse here.
        records.setdefault(key(r), {k: r[k].strip() for k in ("city", "visa", "date", "age")})
    return list(records.values())


def key(record):
    return json.dumps([record[k].strip() for k in ("city", "visa", "date")], ensure_ascii=False, separators=(",", ":"))


def load_state(path):
    if not path.exists():
        raise MonitorError("STATE_MISSING: restore cache or explicitly run baseline mode; no alerts sent")
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        if state.get("schema") != SCHEMA or not state.get("initialized_at") or not isinstance(state.get("seen"), list):
            raise ValueError
        for value in state["seen"]:
            fields = json.loads(value)
            if not isinstance(fields, list) or len(fields) != 3 or not all(isinstance(x, str) and x for x in fields):
                raise ValueError
            dt.date.fromisoformat(fields[2])
        return state
    except (ValueError, TypeError, AttributeError, OSError):
        raise MonitorError("STATE_INVALID: refusing to silently reset history; explicit baseline recovery required") from None


def save_state(path, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def push(title, body):
    topic = env("NTFY_TOPIC")
    if not topic:
        raise MonitorError("PUSH_FAILED: VISA_NTFY_TOPIC is missing")
    # Use JSON publish so Chinese titles do not rely on HTTP header encoding.
    # Topic stays runtime-only and is never printed or written into state.
    payload = {"topic": topic, "title": title, "message": body, "click": URL, "priority": 3}
    request = urllib.request.Request("https://ntfy.sh", data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            result = json.loads(response.read())
            if response.status != 200 or result.get("event") != "message" or not result.get("id"):
                raise ValueError("missing delivery acknowledgement")
    except Exception as exc:
        raise MonitorError(f"PUSH_FAILED: {type(exc).__name__}; no notification acknowledgement") from None


def message(records, snapshot=False):
    title = ("历史快照（非新增提醒）" if snapshot else "新观察到历史成功记录") + " · " + LABEL
    lines = [LABEL, "子类别未知：无法确认普通面谈、免面谈或紧急预约；不能证明现在有位。"]
    shown = 0
    for r in sorted(records, key=lambda r: (r["date"], r["city"], r["visa"])):
        line = f"{r['city']} {r['visa']} · 预约日期 {r['date']} · 页面标注 {r['age']}"
        if len(("\n".join(lines) + "\n" + line).encode("utf-8")) > 3200:
            break
        lines.append(line)
        shown += 1
    if shown < len(records):
        lines.append(f"另有 {len(records) - shown} 条匹配记录未展开；通知按日期排序。")
    if not records:
        lines.append("本次公开历史列表未见符合筛选条件的记录；不代表没有余位。")
    lines.append("仅观察公开历史列表，未查询官方预约系统。")
    return title, "\n".join(lines)


def process(data, mode, path, cities, visa, cutoff, current_date, sender=push):
    records = validate(data)
    stamp = dt.datetime.now(dt.timezone.utc).isoformat()
    matches = [r for r in records if r["city"] in cities and r["visa"] == visa and current_date <= dt.date.fromisoformat(r["date"]) <= cutoff]
    if mode == "status":
        sender(*message(matches, snapshot=True))
        print(f"STATUS_SNAPSHOT_SENT: {len(matches)} matching historical records; {LABEL}")
        return
    if mode == "baseline":
        if path.exists():
            raise MonitorError("BASELINE_REFUSED: state already exists; use monitor mode or deliberate cache recovery")
        state = {"schema": SCHEMA, "initialized_at": stamp, "seen": []}
        print(f"BASELINE: suppressing all {len(records)} pre-existing historical records; no backlog alert")
    else:
        state = load_state(path)
        seen = set(state["seen"])
        fresh = [r for r in matches if key(r) not in seen]
        if fresh:
            sender(*message(fresh))
            print(f"HISTORICAL_ALERT_SENT: {len(fresh)} newly observed date keys; {LABEL}")
        else:
            print(f"HISTORICAL_CHECK_OK: {len(records)} public records parsed; no new matching date keys; {LABEL}")
    # Conservative rolling dedup: retain dates while still eligible to alert,
    # even if they vanish then reappear. Store all categories seen, not only hits.
    # Do not update state before push succeeds, so failed delivery can retry.
    all_seen = set(state["seen"]) | {key(r) for r in records}
    state["seen"] = sorted(k for k in all_seen if dt.date.fromisoformat(json.loads(k)[2]) >= current_date)
    state["last_checked"] = stamp
    save_state(path, state)


def main(argv=None):
    parser = argparse.ArgumentParser(description=LABEL)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--once", action="store_true", help="observe new historical date keys")
    modes.add_argument("--baseline", action="store_true", help="explicitly initialize without backlog alerts")
    modes.add_argument("--check", action="store_true", help="send historical snapshot, never a new-record alert")
    modes.add_argument("--test-push", action="store_true", help="send a clearly labelled transport test")
    args = parser.parse_args(argv)
    try:
        if args.test_push:
            push("推送测试 · " + LABEL, "仅测试通知通道，未发现新记录；子类别未知。")
            print("TEST_PUSH_ACKNOWLEDGED: transport test only")
            return 0
        cities, visa, cutoff = config()
        mode = "baseline" if args.baseline else "status" if args.check else "monitor"
        path = Path(env("STATE", "history-state.json"))
        if mode == "monitor":
            load_state(path)  # fail before network work if continuity is lost
        process(scrape(), mode, path, cities, visa, cutoff, today())
        return 0
    except Exception as exc:
        print(str(exc) if isinstance(exc, MonitorError) else f"MONITOR_FAILED: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
