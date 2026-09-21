# KRX AI Trading Assistant v3 (누수방지 예측 앙상블 · 리서치 학습형 · 자동주문 없음)

한국 주식시장을 대상으로 다음을 자동화하는 Python 프로젝트입니다.

- **07:45 증권사 리포트 전수 스캔**: 당일 공개된 종목분석 리포트의 제목·증권사·목표가·투자의견·공개 요약을 수집하고 전부 AI/규칙 기반으로 분류
- **08:30 장전 분석**: 거래대금·등락률·외국인/기관 수급·일봉 추세·DART 공시·최신 뉴스·증권사 리포트 컨센서스를 함께 반영해 후보 선정
- **09:30~15:00 30분 간격 재평가**: 현재가, 당일 VWAP, 거래량, 시초가 대비 위치, 지지/저항, 추격 위험 재계산
- **Telegram 알림**: 진입 관심 구간, 추격 금지, 손절/무효화, 1·2차 목표가, 권장 비중 + 설정 자금 기준 예상 투입금/주수
- **15:45 장마감 전체 추천 피드백**: 사용자가 실제로 샀는지와 무관하게 아침에 추천한 모든 종목을 평가. `종목선정 품질`과 `진입/손절/목표 전략 품질`을 별도로 기록
- **누수방지 확률/수익률 예측 앙상블(v3)**: 장전 시점에 동결한 shadow 후보만 사용하고, 날짜 단위 walk-forward + embargo로 검증한 Logistic/Gradient Boosting/Random Forest 앙상블이 `상승확률`, `기대 순수익률`, `경험적 예측구간`을 산출
- **선택적 예측(abstention)**: 확률·기대수익·불확실성이 기준을 못 넘으면 종목 수를 억지로 채우지 않고 현금 대기를 허용
- **시장 국면/드리프트 반영**: 시장 breadth·dispersion·risk-on/off·고변동성 국면을 feature로 넣고 최근 분포가 과거와 달라지면 ML 비중을 자동 축소/비활성화
- **온라인 학습(강화학습 유사 baseline)**: 모든 추천 종목의 시가→종가, MFE, MAE를 보상값으로 사용해 해석 가능한 기본 스코어 가중치도 천천히 업데이트. ML과 별도의 안전한 baseline으로 유지
- **증권사 리포트 적중도 학습**: 리포트 방향과 당일 주가 반응을 장기간 누적해 증권사별 단기 트레이딩 유용도 가중치를 좁은 범위에서 천천히 보정
- **자동 주문/매매 기능은 의도적으로 없음**

> 이 프로그램은 투자수익을 보장하지 않습니다. 가격·수급 API 지연, 데이터 오류, 뉴스 해석 오류, 슬리피지와 실제 체결 차이가 있을 수 있습니다. 처음에는 충분한 기간 동안 알림만 기록하고 성과를 검증하세요.

## 1. 준비물

1. Python 3.11 이상
2. 한국투자증권 KIS Developers 앱키/앱시크릿
3. OpenDART API key
4. OpenAI API key (`OPENAI_MODEL`은 최종 후보 분석, `OPENAI_RESEARCH_MODEL`은 대량 리포트 분류용)
5. Telegram Bot token + chat id

KIS 공식 샘플 저장소: https://github.com/koreainvestment/open-trading-api
OpenDART: https://opendart.fss.or.kr/
OpenAI Responses API: https://platform.openai.com/docs/api-reference/responses
Telegram Bot API: https://core.telegram.org/bots/api

## 2. 설치 (Windows PowerShell)

```powershell
cd krx_ai_trading_assistant
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

`.env`를 열어 API 키를 입력합니다. **API 키는 절대 다른 사람에게 보내거나 GitHub에 올리지 마세요.**

## 3. 설정

`config.yaml`에서 아래를 먼저 수정하세요.

- `risk.day_trade_capital_krw`: 단타에 실제로 사용할 신규 자금
- `existing_positions`: 기존 보유 종목. 현재 예시는 HLB 221주/평단 52,800원으로 넣어두었습니다.
- `market.final_pick_count`: 최종 후보 개수
- `prediction.*`: v3 지도학습 예측기, walk-forward/embargo, 확률보정, 드리프트, 선택적 진입 기준
- `learning.*`: 해석 가능한 baseline 가중치의 학습 속도 및 안정장치
- `research.*`: 증권사 리포트 수집 페이지 수, AI 배치 크기, 증권사 신뢰도 학습률 등

현재 HLB가 `risk_class: high`로 등록되어 있어, 새 고변동 종목 비중을 자동으로 더 낮추도록 설계되어 있습니다.

## 4. 먼저 연결 테스트

```powershell
python run.py doctor
```

KIS / DART / OpenAI / 증권사 리서치 공개페이지 / Telegram 연결 여부를 각각 확인합니다.

Telegram chat id 확인은 BotFather에서 bot을 만든 뒤 봇에게 메시지를 한 번 보내고 Bot API의 `getUpdates`를 사용하면 됩니다.

## 5. 수동 실행

증권사 리포트 수집/분석만 테스트:

```powershell
python run.py research
```

장전 분석:

```powershell
python run.py premarket
```

장중 업데이트:

```powershell
python run.py intraday
```

장마감 평가 + 학습:

```powershell
python run.py close
```

학습 상태 확인:

```powershell
python run.py status
```

v3 예측기만 재검증:

```powershell
python run.py validate-model
```

## 6. 자동 스케줄 실행

```powershell
python run.py scheduler
```

프로그램을 계속 켜 두면 `config.yaml` 일정에 따라 자동 실행됩니다.

기본 일정:

- 07:45 당일 증권사 기업리포트 1차 전수 스캔/분석
- 08:30 최신 리포트 재확인 + 장전 종목선정
- 09:30~15:00 30분 간격
- 15:45 장마감 평가/학습

노트북이 꺼지거나 절전 상태면 실행되지 않습니다. 장기 운영은 Windows 작업 스케줄러로 로그인 시 자동 시작하거나 VPS에 올리는 것을 권장합니다.

현재 스케줄러는 **월~금 기준**으로 동작합니다. 한국거래소 휴장일/임시휴장일에는 자동으로 달력 판정을 하지 않으므로, 해당 날 API가 비정상/휴장 데이터를 반환하면 알림을 무시하세요. 휴장일 캘린더 연동은 차기 확장 포인트입니다.

## 7. 종목선정 로직 (v3)

### 7.1 숫자 필터

KIS에서 거래대금 상위, 등락률 상위, 외국인/기관 순매수 상위를 합쳐 후보 풀을 만듭니다. 이후 각 종목의 최근 일봉을 조회해 다음 feature를 0~1 범위로 계산합니다.

- liquidity
- turnover_acceleration
- momentum_1d
- momentum_5d
- trend_quality
- foreign_flow
- institution_flow
- intraday_strength
- news_catalyst
- broker_report_signal
- overheating_inverse
- momentum_10d / volatility_quality / atr_pct_quality
- 20일 고가 근접도·range position·종가 위치·volume pressure
- 후보군 내 cross-sectional percentile rank(유동성·모멘텀·수급·추세·리포트·뉴스)
- market breadth / dispersion / regime risk-on·risk-off·high-vol
- global_risk_on / macro_event_safety

### 7.2 증권사 기업리포트 파이프라인

기본 소스는 Npay 증권의 공개 `종목분석 리포트` 인덱스/상세 페이지입니다. 프로그램은 첨부 PDF를 내려받아 보관하지 않고, 공개 페이지에 표시되는 종목·제목·증권사·작성일·목표가·투자의견·짧은 요약만 개인 분석용으로 사용합니다. 사이트 구조나 이용조건이 바뀌면 `stockbot/research.py` 어댑터를 수정해야 합니다.

당일 리포트는 가능한 범위에서 전부 순회한 뒤 각 보고서마다 다음 값을 만듭니다.

- `sentiment`: -1 ~ +1
- `conviction`: 0 ~ 1
- `estimate_revision`: -1 ~ +1
- 목표가 대비 현재가 괴리
- 같은 종목을 다룬 증권사 수/보고서 수
- 과거에 누적된 증권사별 단기 적중도(0.30~0.70로 강하게 제한)

이들을 합쳐 `broker_report_signal` 0~1을 만들고 종목선정 feature에 포함합니다. 리포트가 있다는 이유만으로 후보가 되지는 않으며, 최소 거래대금/가격/과열 필터를 그대로 통과해야 합니다.

### 7.3 AI의 역할

AI는 **가격 숫자를 임의로 만드는 역할이 아닙니다.** Python이 계산한 후보/가격대/리스크를 받고 최신 뉴스와 공시의 질을 검토해 `news_catalyst`와 설명을 보강합니다. 증권사 리포트도 제공된 공개 요약 안에서 방향성/근거 강도/추정치 변화를 구조화합니다. AI가 실패하면 리포트는 규칙 기반 분류로 폴백하고, 종목선정은 숫자 모델만으로 계속 동작합니다.

### 7.4 가격대

ATR, 최근 저점, 전일 종가를 사용해 1·2차 진입 구간과 무효화 가격을 계산합니다. 목표가는 실제 손절폭에 R 배수를 적용합니다. 장중에는 VWAP와 현재 추세가 깨지면 신규 진입을 막습니다.

## 8. v3 예측 엔진

### 8.1 무엇을 예측하나

한 개의 점수만 예측값처럼 사용하지 않습니다. 각 shadow 후보에 대해 서로 다른 목표를 동시에 추정합니다.

- `up_probability`: 거래비용을 차감한 시가→종가 수익률이 설정 threshold를 넘을 확률
- `expected_return_pct`: 예상 시가→종가 **순수익률(%)**
- `lower_return_pct`, `upper_return_pct`: 최근 OOF 잔차를 이용한 경험적 불확실성 구간
- `model_score`: 확률 + 기대수익 + 하방 불확실성을 결합한 0~100 점수

가격 진입/손절/목표가는 여전히 ATR·최근 저점·VWAP 같은 결정론적 로직이 계산합니다. ML이나 GPT가 임의 가격을 만들어 주문하지 않습니다.

### 8.2 모델 앙상블

분류기는 `LogisticRegression + HistGradientBoostingClassifier + RandomForestClassifier`, 수익률 회귀는 `Ridge + HistGradientBoostingRegressor + RandomForestRegressor`를 사용합니다. 선형 모델과 비선형 트리 모델을 함께 사용해 한 모델의 편향에만 의존하지 않습니다.

각 모델의 혼합비는 **out-of-fold 성능**을 기준으로 정합니다. 확률 분류는 Brier loss, 수익률 회귀는 MAE를 이용합니다.

### 8.3 데이터 누수 방지

이 프로젝트에서 가장 중요한 v3 변경입니다.

1. 08:30 장전 계산 시 전체 shadow 후보의 feature와 예측을 DB에 **최초 1회 동결**합니다.
2. 같은 날 다시 실행해도 `candidate_snapshots`는 덮어쓰지 않습니다.
3. 학습 시 `as_of_date`보다 **엄격하게 이전 거래일 데이터만** 읽습니다.
4. random K-fold를 쓰지 않고 **거래일 단위 expanding walk-forward**를 사용합니다.
5. train과 test 사이에 기본 1거래일 `embargo`를 둡니다.
6. 같은 거래일의 여러 종목은 train/test 양쪽으로 갈라지지 않습니다.
7. 아직 발표되지 않은 CPI/FOMC 등의 결과는 AI prompt에서 알 수 없는 값으로 강제하고, 단지 `scheduled event risk`로만 사용합니다.

### 8.4 확률 보정과 champion/challenger

OOF 구간의 앞부분은 확률 calibration, 뒷부분은 **손대지 않은 최종 validation**으로 분리합니다. 표본이 작을 때는 sigmoid(Logistic), 충분히 커지면 isotonic calibration을 사용합니다.

새 ML 모델이 다음 지표에서 기존 heuristic baseline보다 개선되는지 확인합니다.

- Brier score / calibration error
- AUC / log loss
- 기대수익 MAE / RMSE
- 일별 cross-sectional Rank-IC

개선되지 않으면 ML `blend_weight=0`이 되어 기존 baseline만 사용합니다. 개선되더라도 기본 최대 혼합 비중은 65%입니다.

### 8.5 시장 국면과 drift

후보군 전체의 전일 등락률 분포에서 breadth, dispersion, 고변동 여부를 계산해 `risk_on`, `risk_off`, `high_vol` 국면 feature를 추가합니다. AI가 확인한 글로벌 위험선호와 예정 이벤트 위험도 함께 사용합니다.

최근 feature 분포가 학습 시기와 지나치게 달라지면 drift score에 따라 ML 비중을 절반으로 줄이거나 0으로 만듭니다. 과거에 잘 맞았다는 이유만으로 다른 시장 국면에서도 같은 모델을 강제하지 않습니다.

### 8.6 최소 표본과 현금 대기

기본값은 **400개 shadow 표본 + 20개 서로 다른 거래일**이 쌓이기 전에는 지도학습 모델을 활성화하지 않습니다. 후보 24개/일 기준으로 약 20거래일의 실제 동결 데이터가 필요합니다. 그전에는 baseline 점수만 사용합니다.

또한 `min_up_probability`, `min_expected_return_pct`, `minimum_final_score` 등을 통과하지 못하면 최종 추천을 3개로 억지로 채우지 않습니다. 좋은 setup이 없으면 0~2개 추천 또는 현금 대기가 정상 동작입니다.

### 8.7 수동 모델 검증

```powershell
python run.py validate-model
```

현재 축적 데이터만으로 walk-forward challenger 검증을 다시 실행하고 Brier, MAE, Rank-IC, calibration, drift, 활성 여부를 출력합니다.

## 9. 장마감 학습 방식

이 버전부터는 **실제 매수 여부를 전혀 사용하지 않습니다.** 아침에 최종 추천한 종목은 전부 그날의 모델 성과 표본입니다.

장마감 후 각 추천 종목마다 두 가지를 따로 계산합니다.

### A. 종목선정 평가 — 모든 추천 종목 100% 포함

1. 09:00 첫 분봉의 시가
2. 15:30 마지막 분봉 종가
3. 장중 최고가/최저가
4. 시가→종가 수익률
5. MFE(Maximum Favorable Excursion)
6. MAE(Maximum Adverse Excursion)
7. `selection_reward = 0.65×종가수익률 + 0.20×MFE + 0.15×MAE`를 목표수익률로 정규화해 -1~+1로 제한

따라서 사용자가 실제로 매수하지 않았거나, 프로그램이 제시한 눌림 매수가까지 오지 않았더라도 **추천 종목 자체가 좋았는지는 반드시 학습합니다.**

### B. 매매계획 평가 — 별도 기록

기존처럼 추천 진입구간에 실제 가격이 닿았는지 확인하고, 닿았다면 손절/1차/2차 목표 도달 순서를 분봉으로 보수적으로 시뮬레이션합니다. 같은 분봉에서 손절과 목표가가 동시에 닿으면 손절이 먼저 발생한 것으로 처리합니다. 이 결과는 `strategy_outcomes`로 저장되며 종목선정 평가와 분리됩니다.

### C. feature 가중치 온라인 학습

1. 모든 추천의 `selection_reward`를 가져옴
2. 당시 liquidity, 수급, momentum, trend, news, `broker_report_signal` 등의 feature와 보상을 연결
3. 좋은 보상과 함께 높았던 feature 가중치를 소폭 증가
4. 나쁜 보상과 함께 높았던 feature는 소폭 감소
5. 매일 기본 가중치 쪽으로 일부 회귀
6. 최소 표본 수와 일일 최대 변경폭 적용

즉 한두 번의 우연한 적중으로 전략이 급변하지 않도록 설계했습니다.

### D. 증권사 리포트 자체 피드백

당일 리포트가 나온 종목은 장마감 후 가능한 범위에서 시가→종가 반응을 수집합니다. 리포트의 `sentiment × 실제 당일 수익률`을 이용해 증권사별 단기 트레이딩 유용도를 아주 천천히 업데이트합니다. 이 값은 **0.30~0.70 범위로 제한**되므로 특정 증권사를 절대적으로 신뢰하거나 무시하지 않습니다. 이는 장기 기업가치 분석의 정확도를 평가하는 지표가 아니라, 이 프로그램의 **당일/초단기 종목선정에 얼마나 유용했는지**를 보조 측정하는 값입니다.

DB에는 `recommendations`, `intraday_snapshots`, `outcomes`, `pick_evaluations`, `broker_reports`, `learning_state`, `learning_history`가 저장됩니다.

## 10. 중요한 제한

- KIS API별 호출 제한/응답 컬럼은 변경될 수 있습니다. 공식 GitHub 변경사항을 주기적으로 확인하세요.
- 한국투자 외국인/기관 가집계는 장중 특정 시점에 입력·갱신되는 추정치 성격이 있습니다.
- DART 공시가 없는 종목도 정상입니다.
- OpenAI 웹검색/뉴스 평가와 대량 리포트 분류에는 API 비용이 발생합니다. 기본값은 최종 후보 분석에 `gpt-5.6-terra`, 대량 리포트 분류에 비용 효율적인 `gpt-5.6-luna`를 사용하며 `.env`에서 바꿀 수 있습니다. `research.ai_batch_size`로 한 번에 묶는 수를 조정할 수 있습니다.
- 공개 리서치 페이지/HTML 구조 및 이용조건은 제공자가 바꿀 수 있습니다. 본 프로그램은 개인 분석 목적이며 리포트 PDF 재배포 기능을 포함하지 않습니다.
- 장전 08:30에는 일부 장중 정보(VWAP 등)가 없으므로 중립값을 사용합니다.
- 신규 상장/거래정지/관리종목/급격한 호가 공백 등은 사람이 최종 확인해야 합니다.
- 이 버전은 **주문 API를 전혀 호출하지 않습니다.** 소스에도 주문 엔드포인트를 포함하지 않았습니다.
- 온라인 학습은 이 프로젝트 내부의 `base_weights`를 조정할 뿐, OpenAI 모델 자체를 파인튜닝하거나 재학습하지 않습니다.
- v3 지도학습 앙상블도 **미래 수익을 보장하지 않습니다.** 활성화 조건은 “과거의 누수방지 validation에서 baseline을 이겼다”는 뜻일 뿐, 이후 시장에서 동일 성능을 보장하지 않습니다.
- 경험적 예측구간은 비정상(non-stationary) 금융시장에서 엄밀한 coverage 보장을 하는 conformal prediction이 아니라, 최근 OOF 잔차 분위수를 이용한 보수적 참고 구간입니다.

## 11. 테스트

```powershell
pytest -q
```

실제 API 키 없이도 학습·스코어·리포트 파서 핵심 테스트는 실행됩니다. 현재 구성 기준 **14개 테스트**가 포함되어 있으며, 날짜 누수 방지 split, 당일 snapshot 불변성, 비선형 synthetic signal 학습, cross-sectional rank 등을 별도로 검사합니다.

## 파일 구조

```text
krx_ai_trading_assistant/
├── run.py
├── config.yaml
├── .env.example
├── requirements.txt
├── README.md
├── stockbot/
│   ├── ai.py
│   ├── config.py
│   ├── dart.py
│   ├── db.py
│   ├── features.py
│   ├── jobs.py
│   ├── kis.py
│   ├── learner.py
│   ├── models.py
│   ├── notifier.py
│   ├── risk.py
│   ├── research.py
│   ├── scoring.py
│   └── scheduler.py
└── tests/
```
