# v7.9 — Reliability + 15-minute Telegram heartbeat

## Why this release exists

Public GitHub Actions history showed that the Korean monitor was generally healthy, but the US path was not consistently finishing inside its execution window. A recent US intraday run exceeded its 10-minute job limit, a US daily run exceeded 12 minutes, and manual refresh jobs could occupy the shared cloud-state concurrency queue long enough to delay a scheduled monitor.

## Changes

### FRED circuit breaker
- One short FRED preflight is performed before requesting all macro series.
- If the official API is unavailable, a single graph-CSV fallback probe is attempted.
- If both transports are unavailable, the job immediately reuses the previous good macro snapshot instead of launching a timeout/retry storm across every series.
- Persisted macro snapshots are now used as a serverless cache, so a new GitHub runner does not refetch slow-moving FRED data on every 15-minute market job.
- Phone `refresh-kr` / `refresh-us` still forces live KIS/index/stock refreshes, but no longer forces a full FRED refresh every click. The dedicated `macro` job still forces FRED.

### Faster KIS failure handling
For intraday/cloud workflows:
- retries: 2
- connect timeout: 6 seconds
- read timeout: 12 seconds
- network circuit cooldown: 180 seconds

A KIS outage therefore degrades to saved/stale state sooner instead of consuming most of a 15-minute interval.

### Telegram every scheduled intraday run
- KR and US cloud intraday jobs always request a Telegram summary.
- A message is sent even when there is no recommendation.
- When available, the message includes the strongest near-miss candidate and required score.
- Every heartbeat ends with `✅ 15분 모니터 정상 실행`.
- Workflow failures also send a red Telegram alert with the GitHub run URL.

The schedule is during the configured KR/US market monitoring windows, not 24 hours a day. GitHub scheduled workflows can still start late under platform load.

### Workflow reliability
- KR/US intraday schedules remain 15-minute cadence in their local market time zones.
- Intraday timeout raised to 12 minutes while API failures themselves fail faster.
- Daily and manual jobs receive the same fail-fast KIS environment and Telegram failure alerts.
- Shared cloud-state serialization is retained to avoid concurrent writers corrupting/overwriting the Supabase state bundle.
