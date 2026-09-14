# v7.3 — GitHub Pages PWA hosting fix

## Why this release exists
Supabase Storage intentionally serves uploaded HTML as plain text for security. Therefore it is suitable for the public JSON feed and state objects, but not for hosting the PWA entry HTML.

## Changes
- PWA deployment moved from Supabase Storage to GitHub Pages.
- Supabase remains the cloud state/data backend.
- `SUPABASE_URL` is injected into a generated `runtime-config.js` during the Pages build.
- No Supabase secret key is exposed to the browser.
- Serverless monitor fallback labels changed from 10 minutes to 15 minutes.
- Service-worker cache version bumped to `krx-ai-v7.3-static`.

## Resulting architecture
GitHub Actions -> KIS/DART/FRED -> Supabase state + dashboard JSON
GitHub Pages -> PWA HTML/CSS/JS -> reads public Supabase dashboard JSON
