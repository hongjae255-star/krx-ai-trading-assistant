# v7.8 one-time setup

## 1. FRED macro reliability
Create a free FRED API key, then add it to GitHub:

Repository → Settings → Secrets and variables → Actions → New repository secret

- Name: `FRED_API_KEY`
- Value: your 32-character lowercase FRED API key

The workflows already pass this secret to the Python process. The dashboard will show `FRED_API` and `API key: valid` when active.

## 2. Secure manual refresh button
The PWA must not contain a GitHub token. v7.8 therefore uses a Supabase Edge Function as the secure bridge.

### GitHub fine-grained token
Create a fine-grained personal access token limited to:
- Repository: `hongjae255-star/krx-ai-trading-assistant`
- Repository permission: **Actions — Read and write**

Do not put this token in `web/`, `runtime-config.js`, or any public GitHub file.

### Supabase Edge Function secrets
In Supabase Dashboard → Edge Functions → Secrets, add:

- `GITHUB_ACTIONS_TOKEN` = the fine-grained token above
- `MANUAL_REFRESH_KEY` = a long private key/passphrase you choose (16+ characters recommended)
- `GITHUB_OWNER` = `hongjae255-star`
- `GITHUB_REPO` = `krx-ai-trading-assistant`
- `GITHUB_REF` = `main`

### Deploy the Edge Function from GitHub Actions
Add these GitHub repository secrets:

- `SUPABASE_ACCESS_TOKEN` = your Supabase account access token
- `SUPABASE_PROJECT_ID` = your project ref (for the current project: `letzwiibdgjvltqlkgev`)

Then run:

Actions → **Deploy manual refresh Edge Function** → Run workflow

### Use it on iPhone
After GitHub Pages finishes deploying v7.8:
1. Open the PWA.
2. Tap ↻.
3. On first use, enter the same `MANUAL_REFRESH_KEY` you stored in Supabase.
4. The button immediately dispatches a GitHub Action for the selected KR/US tab.
5. The app polls the public dashboard and refreshes itself when the new publication appears.

The action usually needs tens of seconds to a few minutes; the request itself is immediate, but GitHub runner queue/API latency cannot be made zero-delay.
