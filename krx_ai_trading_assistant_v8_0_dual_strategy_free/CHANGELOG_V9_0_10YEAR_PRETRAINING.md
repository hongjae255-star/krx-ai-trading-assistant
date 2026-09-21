# v9.0 — 10-Year Korea/US Pretraining

## Added
- Free 10-year OHLCV downloader for KR (KOSPI/KOSDAQ) and US (NASDAQ/NYSE/AMEX current listings).
- Resumable per-symbol compressed raw storage under `data/historical/raw/`.
- Leakage-safe feature/label builder.
- Four pretraining bundles: KR-day, KR-swing, US-day, US-swing.
- Expanding walk-forward validation before final deployment refit.
- Recency weighting so recent history matters more without discarding old regimes.
- Live strategy-lane blending via `HistoricalModelRegistry` (default historical weight 30%).
- Website field: `10년 패턴 유사 성공률` when a trained bundle is available.
- Conservative day label: next-session +1% MFE while not touching -1% MAE.
- 10-session swing label: MFE >= +4% and MAE > -6%.
- Adjusted OHLC download to reduce split/dividend distortions.

## Safety / validity notes
- Current-listing universe creates survivorship bias for old history. Treat historical metrics as model diagnostics, not guaranteed future performance.
- Daily bars cannot identify intraday ordering when target and stop are both hit; the label intentionally treats those ambiguous sessions conservatively.
- Models are advisory signals only. The existing live risk gates, abstention, and daily adaptive learner remain active.
