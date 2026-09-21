# v7.8 — Macro resilience + secure manual refresh

## Global macro/FRED
- Validates `FRED_API_KEY` before using the official API.
- Uses one HTTP session per worker instead of sharing a `requests.Session` across threads.
- Official API and public graph fallback now use different concurrency limits.
- Longer connect/read timeouts and one additional retry for GitHub-hosted runners.
- Separates **transport failures** from **hard data failures**.
- If a previous valid series exists, a temporary FRED failure reuses it as `STALE` instead of zeroing it.
- Dashboard now shows `fresh / previous / hard fail` rather than calling every recovered series a failure.
- Fixes missing `KISNetworkError` import in the live macro proxy path.

## Manual refresh
- Added a ↻ button to the PWA header.
- Button securely triggers the existing GitHub Actions cloud workflow through a Supabase Edge Function.
- Refreshes the selected market plus global macro, live cross-asset proxies, index pulse, candidate scan, and dashboard publication.
- The browser never receives or stores the GitHub Actions token.
- The app polls `dashboard.json` and redraws automatically when the new publication arrives.

## Security
- Manual refresh uses a user-defined `MANUAL_REFRESH_KEY` checked inside Supabase Edge Functions.
- GitHub token is stored only as the Supabase secret `GITHUB_ACTIONS_TOKEN`.
- Use a fine-grained PAT limited to this repository with Actions: write.
- No order/trading endpoints were added.
