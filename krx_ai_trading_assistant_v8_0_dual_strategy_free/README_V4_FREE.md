# KRX AI Trading Assistant v4 FREE

OpenAI API와 유료 LLM 호출을 완전히 제거한 무료 운영판입니다. 자동주문 기능은 없습니다.

## 무엇으로 분석하나

- **한국투자 KIS**: 현재가, 거래량, 거래대금, 외국인/기관 수급, 일봉/분봉
- **OpenDART**: 최근 기업 공시
- **Naver Finance 공개 기업리포트 페이지**: 증권사 리포트 제목/메타데이터/공개 요약
- **Naver Finance 공개 종목뉴스 목록**: 제목 메타데이터만 best-effort 수집
- **로컬 Rule/Event Engine**: 수주/계약/실적상향/유증/CB/거래정지 등 이벤트 점수화
- **scikit-learn 앙상블**: 상승확률, 기대수익률, MFE/MAE 예측
- **자체 사후학습**: 실제 매수 여부와 무관하게 추천/shadow 후보를 장마감 후 평가
- **Telegram**: 알림

## 비용

프로그램 자체와 로컬 ML/규칙 엔진은 무료입니다. OpenAI API 키나 크레딧은 전혀 필요하지 않습니다. KIS/OpenDART/Telegram의 계정 및 이용조건은 각 서비스 정책을 따릅니다. 공개 웹페이지 수집은 사이트 구조 변경 시 일부 기능이 일시적으로 실패할 수 있으며, 프로그램은 이 경우에도 수치 모델로 계속 동작합니다.

## 설치 (Windows)

```powershell
cd C:\경로\krx_ai_trading_assistant_v4_free
py -3.11 -m venv .venv
Set-ExecutionPolicy -Scope Process Bypass
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
```

`.env`에는 아래만 필요합니다.

```text
FREE_MODE=true
KIS_APP_KEY=...
KIS_APP_SECRET=...
DART_API_KEY=...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

OpenAI 관련 환경변수는 없습니다.

## 연결 확인

```powershell
python run.py doctor
```

정상 예시:

```json
{
  "KIS": true,
  "DART": true,
  "AnalysisEngine": "LOCAL RULE/ML ENGINE OK",
  "OpenAI": "DISABLED (Free Mode)",
  "BrokerResearchSource": true,
  "PredictiveML": "scikit-learn ...",
  "Telegram": true
}
```

`OpenAI: DISABLED (Free Mode)`는 오류가 아니라 **정상 상태**입니다.

## 실행

```powershell
python run.py research       # 증권사 리포트 무료 로컬 분석
python run.py premarket      # 장전 후보 선정
python run.py intraday       # 장중 재평가
python run.py close          # 장마감 평가 + 학습
python run.py status         # 누적 성과/모델 상태
python run.py validate-model # walk-forward 모델 검증
python run.py scheduler      # 자동 스케줄러
```

기본 스케줄:

- 07:45 기업리포트 수집/분석
- 08:30 장전 후보 선정
- 09:30~15:00 30분 간격 장중 재평가
- 15:45 모든 추천/shadow 후보 평가 + 학습

## 무료 로컬 이벤트 엔진

LLM 대신 공개 텍스트에서 다음과 같은 이벤트를 정량화합니다.

긍정 예: `공급계약`, `단일판매`, `수주`, `자사주 취득`, `흑자전환`, `실적 상향`, `목표가 상향`, `어닝 서프라이즈`.

부정/위험 예: `유상증자`, `전환사채`, `신주인수권`, `거래정지`, `상장폐지`, `횡령`, `배임`, `적자전환`, `목표가 하향`.

공시(DART)는 뉴스 제목보다 더 높은 증거 가중치를 받고, 증권사 리포트는 conviction/추정치 변화/목표가/증권사별 누적 단기 적중도를 함께 사용합니다. `event_positive_strength`, `event_risk_inverse`가 ML feature로 저장되어 장기간 실제 결과에 따라 모델이 이 신호의 유용성을 학습합니다.

## 정확도 관련 중요한 제한

무료판은 OpenAI 웹검색을 사용하지 않으므로 미국 CPI/FOMC 같은 당일 글로벌 이벤트와 해외 뉴스의 맥락을 자동으로 완벽히 파악하지 못합니다. 기본적으로 글로벌 risk-on 및 macro event risk를 중립값으로 두고 국내 가격·수급·공시·리포트에 더 의존합니다. 따라서 중요한 매크로 이벤트가 있는 날은 사람이 별도로 확인하는 것이 안전합니다.

또한 어떤 모델도 단기 주가를 안정적으로 보장해 예측할 수 없습니다. 이 프로그램은 추천 정확도를 측정하고 walk-forward 검증에서 개선이 확인된 모델만 반영하도록 설계된 분석 도구입니다.
