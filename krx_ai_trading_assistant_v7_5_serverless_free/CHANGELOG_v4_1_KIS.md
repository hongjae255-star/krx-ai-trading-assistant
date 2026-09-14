# v4.1 FREE - KIS reliability patch

- Added retry handling for HTTP 429/500/502/503/504.
- Added exponential backoff + jitter for temporary KIS/network failures.
- Added explicit EGW00201 rate-limit handling with configurable wait.
- Increased default REST pacing to 0.15 seconds between calls.
- Added a lock around request pacing for future concurrent jobs.
- Added response-body excerpts to final KIS errors for easier diagnosis.
- Added one-time token refresh handling for HTTP 401 inside retry loop.
- No order/trading endpoint added; client remains read-only.

Optional .env overrides:
KIS_REQUEST_INTERVAL=0.15
KIS_MAX_RETRIES=4
KIS_RATE_LIMIT_WAIT=61
