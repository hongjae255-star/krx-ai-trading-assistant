# v7.7 upgrade from your current GitHub clone

After extracting the v7.7 ZIP, run PowerShell from the extracted v7.7 folder:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\upgrade_to_v7_7.ps1 -TargetRepo "C:\Users\nohon\Downloads\krx-ai-v75"
```

Replace the path with your actual local GitHub clone.

The script preserves:
- `.git`
- `.env`
- `.venv`
- `data/` runtime DB/models

Then run:

```powershell
cd "C:\Users\nohon\Downloads\krx-ai-v75"
git add -A
git commit -m "Upgrade to v7.7 decision dashboard"
git push origin main
```

After the push:
1. Wait for **Deploy mobile PWA to GitHub Pages** to become green.
2. Run **Cloud manual job → publish** once. This refreshes Market Pulse/index charts without forcing a stock scan.
3. Run **kr-intraday / us-intraday with full_scan=true** when you want fresh near-miss diagnostics.
4. Reopen the PWA. Service worker cache is bumped to `v7.7`.
