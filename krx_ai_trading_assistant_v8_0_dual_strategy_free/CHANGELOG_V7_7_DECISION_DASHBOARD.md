# v7.7 Decision Dashboard

## Added
- **Top-5 rejected candidates** for KR/US with score gap, ML probability/expected return when available, risk flags and exact abstention reason.
- Intraday replacement scans now persist their strongest near-misses; the app can distinguish "no scan" from "scanned but below threshold".
- **Market Pulse** with 30-session index charts:
  - KOSPI / KOSDAQ via KIS domestic index API.
  - NASDAQ / S&P 500 / Dow / Russell 2000 via KIS overseas index API.
  - US ETF proxy fallback (QQQ/SPY/DIA/IWM) is clearly labeled when a direct index is unavailable.
- Index regime metrics: 5D return, MA20 distance, count above MA20, count positive over 5D, Risk-on/Mixed/Risk-off score.
- Growth leadership: KOSDAQ vs KOSPI and NASDAQ vs S&P 500 5D relative strength.
- Data-health strip for FRED, KIS live proxies, index feeds, KR scan and US scan.
- Stale index data stays visible with a **STALE** label instead of disappearing during a temporary network outage.

## Fixed
- KR intraday leader card is now cleared after a later full scan no longer finds a qualifying leader.
- "현재 신규 추천 없음" is now explicitly separated from "scan failed" and links conceptually to the abstention diagnostics.

## Design choice
The new index/regime signals are displayed and persisted, but are **not automatically injected into the scoring model yet**. Adding unvalidated factors directly to trade scores can increase overfitting; first collect history and validate incremental predictive value.
