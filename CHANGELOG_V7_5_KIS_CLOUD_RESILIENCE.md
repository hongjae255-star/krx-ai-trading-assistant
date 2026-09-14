# v7.5 — KIS cloud-runner resilience

- Added `KISNetworkError` to distinguish TCP/connectivity failures from normal API responses.
- Added configurable connect/read timeouts and a 120-second circuit breaker after repeated KIS network failures.
- Exchange fallback is no longer repeated when the failure is a network-level timeout; changing NYS/NAS/AMS cannot fix a TCP outage.
- Global live macro proxy layer reuses the last good Supabase-restored snapshot when KIS is temporarily unreachable.
- US discovery/intraday/close loops stop quickly during a KIS network outage instead of spending minutes retrying every ETF or stock.
- No order endpoint added; the project remains analysis-only.
