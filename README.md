# KRX AI Trading Assistant v7 — SERVERLESS GLOBAL FREE

**PC/VPS를 24시간 켜놓지 않고** 한국장 + 미국장 + 글로벌 매크로를 모니터링하는 무료 서버리스 버전입니다.

핵심 구조:

```text
GitHub Actions (15분)
        │
        ├── KIS 한국/미국
        ├── OpenDART
        ├── FRED
        ├── 증권사 리포트
        └── Local ML
                │
                ▼
       Supabase private Storage
        SQLite / models / state
                │
                ├──── Telegram
                │
                ▼
       Supabase public Storage
      dashboard.json + PWA
                │
                ▼
             휴대폰
```

- OpenAI API: **사용 안 함**
- 자동매매: **없음**
- 추천종목 실제 매수 여부와 무관하게 사후평가/학습
- 한국/미국 모델 별도 학습
- 금리/채권/VIX/FX/신용/유동성/원자재 + 장중 ETF proxy 반영
- GitHub Actions runner가 매번 사라져도 Supabase에서 state 복원

## 가장 먼저 읽을 파일

**`SETUP_V7_SERVERLESS.md`**

## Gold 404 문제

v6의 폐기 FRED series `GOLDAMGBD228NLBM`을 제거했습니다. v7은 `KCROROG`와 `GLD`를 사용합니다.

## 테스트

```bash
pytest -q
```

## 로컬 모드

기존 로컬 PC 방식도 유지됩니다.

```bash
python run.py doctor
python run.py app
```

## v7.3 PWA hosting note
The mobile PWA is deployed with **GitHub Pages**, not Supabase Storage. Supabase Storage intentionally serves HTML as plain text, so it is used only for state and dashboard JSON. Run the `Deploy mobile PWA to GitHub Pages` workflow after enabling Pages with **Settings -> Pages -> Source: GitHub Actions**.
