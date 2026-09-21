# Upgrade to v7.9

v7.9 is cumulative: it contains v7.7 Decision Dashboard and v7.8 manual-refresh/FRED work.

## Files that changed materially

- `stockbot/global_macro.py`
- `stockbot/cloud_runner.py`
- `stockbot/jobs.py`
- `stockbot/us_market.py`
- `config.yaml`
- `.github/workflows/krx-intraday.yml`
- `.github/workflows/us-intraday.yml`
- `.github/workflows/krx-daily.yml`
- `.github/workflows/us-daily.yml`
- `.github/workflows/cloud-manual.yml`

The easiest safe upgrade is to copy the full v7.9 tree into the existing clone while preserving `.git`, `.env`, `.venv`, and `data`.

## Existing secrets

No new Telegram secret is required. Existing repository secrets are used:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

Keep the existing KIS/FRED/Supabase secrets as before.

## Verification

After pushing, manually run `KRX 15-minute monitor` once and `US 15-minute monitor` once. Each successful run should send a Telegram heartbeat, even if there is no stock recommendation. A failed workflow should send a failure alert.
