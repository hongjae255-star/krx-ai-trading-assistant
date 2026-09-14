# v7.4 — FRED resilience

- Adds retry/backoff for transient FRED timeouts and 429/5xx responses.
- Fetches macro series concurrently (default 6 workers) so one outage does not stall all series sequentially.
- Reuses the last successful macro series stats from restored cloud state when a series is temporarily unavailable.
- Prevents transient FRED outages from replacing good macro state with zeros.
- If `FRED_API_KEY` is configured, the official API is primary; current snapshots can fall back to graph CSV if needed.
- Historical `as_of` requests never fall back to current-vintage CSV, preserving vintage safety.
- Adds `fresh_series_count`, `stale_series_count`, and `stale_series` diagnostics.
