# visaMonitor: public historical-success records

**历史成功记录，非实时余位。** This version reads only the public “最近抢位成功记录” ticker on [qmq.app](https://qmq.app). It does not query or bypass the protected live-availability grid and does not book appointments.

A notification means a city/type/appointment-date combination was newly observed by this monitor. It does not establish when the booking happened, whether a release is ongoing, or whether any appointment is currently available. The ticker does not show interview subtype: **子类别未知，无法确认普通面谈、免面谈或紧急预约**. Its relative label is displayed exactly as reported, without inferring an event time, and is not a reliable unique record identifier.

## GitHub Actions setup

1. Keep the ntfy topic in the repository Actions **secret** `VISA_NTFY_TOPIC`. Never commit it, print it, or put it in a repository variable. Subscribe to that same topic in your ntfy app. An unguessable anonymous topic is a bearer-like private channel, not an access-controlled account.
2. Set repository **variables** `VISA_CITIES` (comma-separated Chinese city names), `VISA_TYPE` (exact badge, for example `B1/B2`), and `VISA_CUTOFF` (inclusive ISO appointment date). To request dates strictly before a day, set the preceding day as the cutoff. Past appointment dates, evaluated in Asia/Shanghai, never alert.
3. Run the **visaMonitor Historical Records** workflow manually with mode **baseline**. This is an explicit initialization: all existing ticker records become the dedup baseline and **no backlog alert is sent**. Verify `BASELINE: suppressing ...` and successful cache saving. Do not treat this as push verification.
4. Run mode **test** to test notification delivery, or **status** to receive a clearly marked historical snapshot. Neither modifies dedup state. An ntfy server acknowledgement does not prove the phone displayed the notification.
5. Keep repository variable `VISA_HISTORY_ENABLED` unset until baseline, cache restoration, snapshot/test push, and a manual monitor run pass. Then set `VISA_HISTORY_ENABLED=1` to allow scheduled monitoring. Removing it pauses scheduled source checks; manual runs remain available.
6. Scheduled **monitor** runs request a check every five minutes. GitHub can delay or skip scheduled runs; this is not an exact five-minute or continuous monitoring guarantee.

## Dedup and recovery

`history-state.json` uses a separate `visamonitor-history-v2-` Actions cache namespace. Old live-grid state is never reused. Each successful observation saves a new rolling cache; workflow concurrency serializes runs. City + exact visa type + ISO appointment date is the conservative identity. Duplicate marquee copies, relative-label changes, and disappearing/reappearing cards do not alert again while their appointment dates remain eligible. Distinct real bookings for the same combination cannot be distinguished and are intentionally suppressed. Changing filters does not replay records already seen.

The first run requires explicit baseline mode. **A scheduled run with missing or corrupt state fails visibly; it never silently creates another baseline.** Actions cache is best-effort storage and can be evicted. If a cache disappears, restore a trusted state if available or manually run baseline again to deliberately suppress the current backlog. For corrupt state, remove only the affected history cache through GitHub's cache controls before re-baselining. Re-baselining loses continuity and can miss records around the gap. Public repositories' caches/logs should not be treated as confidential; only public record keys are cached, never the ntfy topic.

Push failures leave state unchanged for retry. If ntfy accepts a message but the connection fails before acknowledgement, a retry may duplicate that notification. A cache-save failure after a successful push may also cause duplicates; inspect the save and exact-key cache verification steps. A failed verification makes the workflow fail visibly. Cache persistence cannot provide exactly-once delivery.

## Commands and tests

```sh
pip install -r requirements.txt
python -m playwright install chromium
python -m unittest discover -s tests -v
python monitor.py --baseline   # initialize once, no push
python monitor.py --once       # check against existing state
python monitor.py --check      # explicit historical snapshot
python monitor.py --test-push  # explicit transport test, no source observation
```

Configuration comes from environment variables above; `VISA_STATE` optionally changes the local state path. Keep runtime secrets outside tracked files. The previous watch/phone-command/emergency-status modes and subtype filters are unsupported because the public ticker cannot provide those semantics. The new workflow deliberately does not map legacy `VISA_DESC`, `VISA_DESC_BY_CITY`, emergency, email-forwarding, repeat, or burst variables. Locally supplied subtype filters fail explicitly rather than misclassifying records. Existing launchd templates and helper scripts from the upstream repository are legacy and should not be used with this version.

## Failure behavior and limitations

- Fetch, parse, empty/missing ticker, invalid date, state corruption, or failed push return **nonzero**. An unreadable source never reports “no records” as a green success.
- A readable public ticker with zero matching records reports only that this finite historical list has no new matching date keys. It does not establish appointment availability or full source coverage.
- Long notifications list earliest dates first and explicitly count any omitted records to stay within ntfy message limits.
- The ticker may be old, delayed, incomplete, or omit records between polls. The relative label alone does not prove that its publication pipeline is healthy or broken.
- No protected-grid interaction, CAPTCHA solving, browser stealth, token reuse, proxies, or authentication are implemented. If the public ticker becomes restricted, stop and review the failure.
- Check GitHub Actions failure notifications and logs. The monitor cannot guarantee alerts during source, runner, cache, ntfy, or phone-delivery outages.

Reference for the historical-card shape: [upstream adaptation commit](https://github.com/weltond/visamonitor/commit/ab2ab3f6316d07c9e4ba668c725f4130dfc4fb79). This implementation independently scopes reads to the public historical section and does not adopt claims that a historical record proves current slot releases.
