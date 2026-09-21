# v8.0 Strategy Research Note

## 목표

v8.0은 하나의 점수로 모든 시간대를 해결하려 하지 않는다. 서로 다른 두 목표를 분리한다.

1. **당일 +1% 목표 lane** — 비용 차감 후 +1%를 목표로 할 수 있는 당일 셋업의 준비도를 평가한다.
2. **1–2주 Swing lane** — 5–10거래일 동안 추세가 이어질 가능성이 상대적으로 높은 종목을 선별한다.

**+1%는 보장 수익률이 아니다.** 목표가/손절가는 계획값이며 실제 체결, 갭, 슬리피지, 유동성, 뉴스 때문에 결과가 달라진다. 자동 주문은 구현하지 않는다.

---

## 왜 두 전략을 분리했나

당일 거래에 중요한 요소와 1–2주 보유에 중요한 요소의 시간축이 다르다.

- 당일: 유동성, 거래대금 가속, 거래량, 당일/5일 모멘텀, VWAP/장중 강도, ATR, 과열 여부.
- 1–2주: 50/150/200일 추세, 20/60/120일 모멘텀, 상대강도, 52주 고가 근접, 변동성 수축, 촉매, 시장 방향.

둘을 하나의 점수에 섞으면 단타 신호가 느려지고, 스윙 신호가 잡음에 민감해질 수 있어 lane을 분리했다.

---

## 사용한 공개 연구/방법론

### Momentum / Relative Strength

AQR의 *Value and Momentum Everywhere*는 다양한 시장/자산에서 momentum premium의 일관성을 보고한다. v8.0은 이 원칙을 그대로 수익 보장으로 해석하지 않고, 20/60/120일 모멘텀과 cross-sectional relative strength를 스윙 score의 일부로 사용한다.

Source: https://www.aqr.com/Insights/Research/Journal-Article/Value-and-Momentum-Everywhere

### O'Neil / CAN SLIM inspired screening

Investor's Business Daily가 공개적으로 설명하는 CAN SLIM/IBD 방식은 earnings/fundamentals, relative strength, accumulation/distribution, institutional sponsorship, industry/market direction, proper chart setup을 함께 본다. v8.0은 proprietary IBD rating을 복제하지 않고 무료 데이터로 가능한 proxy만 사용한다.

- C/A: 현재 무료 파이프라인에서는 회계 EPS growth 전체를 복제하지 않고 broker revision/catalyst/event proxy를 사용.
- N: 신규 이벤트/촉매.
- S: 거래량/거래대금/수급.
- L: relative strength와 trend rank.
- I: 미국에서 SEC 13F 기관 보유 변화는 작은 보조요인.
- M: 글로벌 macro + live market regime.

Sources:
- https://www.investors.com/how-to-invest/how-to-buy-stocks-using-stock-lists-stock-ratings-stock-screener/
- https://get.investors.com/wp-content/uploads/2024/08/IBDD-How-to-buy-Stocks-infographic.pdf

### Minervini Stage-2 / Trend Template inspired checks

v8.0 swing lane은 다음 공개적으로 널리 알려진 trend-template 성격의 조건을 확인한다.

- 현재가 > MA150, MA200
- MA150 > MA200
- MA200 상승
- MA50 > MA150, MA200
- 현재가 > MA50
- 52주 저점보다 충분히 높음
- 52주 고점에서 너무 멀지 않음
- cross-sectional relative strength가 강함

이 조건을 **절대적인 매수 신호로 사용하지 않고** 전체 swing score의 24%만 차지하게 했다.

### 52-week high / breakout / supply-demand

IBD가 공개하는 chart/breakout 교육은 고가 영역과 거래량 증가를 함께 보는 것을 강조한다. v8.0은 proprietary chart-pattern recognition을 흉내내지 않고 52주 고가 거리, 20일 고가 접근, 거래량/거래대금 가속, 최근 변동성 수축을 수치화한다.

### Quality / profitability principle

Novy-Marx 연구는 gross profitability가 cross-section의 평균수익과 강한 관계를 가질 수 있음을 보여준다. 현재 v8.0 free pipeline에는 모든 한국/미국 종목의 동일한 회계항목을 안정적으로 실시간 수집하는 계층이 아직 없으므로, 이 연구를 근거로 수익률을 직접 예측한다고 주장하지 않는다. 대신 event-risk inverse, volatility quality, overheating inverse를 **quality/risk proxy**로 제한적으로 사용한다. 향후 DART/SEC XBRL 회계계층을 붙일 때 실제 profitability factor로 교체할 수 있도록 score 항목을 분리했다.

Source: https://www.nber.org/papers/w15940

### Market direction / regime

개별 종목이 강해도 시장 전체가 risk-off이면 진입 기준을 높인다. v8.0은 Global Macro, live ETF proxies, KOSPI/KOSDAQ/Nasdaq/S&P500 pulse를 이용해 risk-on / mixed / risk-off를 나누고 ACTIONABLE 문턱을 동적으로 조정한다.

---

## 당일 +1% 목표 알고리즘

기본값:

- net target: +1.00%
- estimated round-trip cost: 25bp
- gross target: 약 +1.25%
- Top 3 표시
- Actionable threshold: Risk-on 58 / Mixed 61 / Risk-off 65

Score weights:

| 요소 | 비중 |
|---|---:|
| Liquidity | 13% |
| Turnover acceleration | 13% |
| Volume pressure | 12% |
| Short momentum | 14% |
| Trend quality | 10% |
| Flow | 8% |
| Near-high / range position | 7% |
| ATR target reachability | 12% |
| Market support | 6% |
| Overheat/event-risk inverse | 5% |

ATR은 단순히 높을수록 좋다고 보지 않는다. +1.25% gross target을 달성할 일중 range가 지나치게 작은 종목은 감점하고, 반대로 ATR이 지나치게 큰 종목은 gap/tail risk 때문에 감점한다.

손절값은 자동 주문이 아니라 **planning stop**이다.

---

## 1–2주 Swing 알고리즘

기본값:

- horizon: 5–10 거래일
- Top 3 표시
- 260 일봉 history
- API 부하 제한을 위해 deep history는 KR Top 10 / US Top 6만 조회
- Actionable threshold: Risk-on 64 / Mixed 68 / Risk-off 72

Score weights:

| 요소 | 비중 |
|---|---:|
| Stage-2 trend template | 24% |
| 20/60/120d momentum | 15% |
| Relative strength | 11% |
| 52-week high proximity | 8% |
| CAN-SLIM-inspired proxy | 12% |
| Demand / volume / flow | 8% |
| Quality/risk proxy | 6% |
| Market regime | 6% |
| SEC 13F context | 4% |
| Volatility contraction | 6% |

13F를 4%로 작게 제한한 이유는 시차 때문이다. SEC에 따르면 Form 13F는 분기말 이후 45일 이내 제출되므로 실시간 기관 매매 데이터가 아니다.

Source: https://www.sec.gov/rules-regulations/staff-guidance/division-investment-management-frequently-asked-questions/frequently-asked-questions-about-form-13f

---

## 유명 투자회사 13F tracker

기본 manager:

- Berkshire Hathaway
- Bridgewater Associates
- ARK Investment Management
- Pershing Square
- Baupost Group

데이터는 SEC EDGAR의 `data.sec.gov/submissions/CIK##########.json`과 13F information table XML을 사용한다. SEC EDGAR data APIs 자체는 API key가 필요 없지만 자동 클라이언트는 연락 가능한 User-Agent를 제공해야 한다.

Source: https://www.sec.gov/search-filings/edgar-application-programming-interfaces

추적 상태:

- NEW: 이전 분기에는 없었고 최신 분기에 존재
- ADD: shares가 2% 초과 증가
- REDUCE: shares가 2% 초과 감소
- EXIT: 최신 분기에서 사라짐

13F를 단독 추천 트리거로 사용하지 않는다.

---

## “맨날 추천 없음” 문제를 어떻게 해결했나

기존 formal recommendation은 `allow_abstain=true`라서 엄격 기준을 아무도 못 넘으면 0개가 정상이다. 이것을 무작정 기준 하향으로 고치면 품질이 나쁜 날에도 억지 신호가 생긴다.

v8.0은 다음처럼 분리한다.

- **Formal execution recommendation:** 기존 엄격 기준 유지. 0개 가능.
- **Dual strategy lanes:** 데이터가 있으면 Top 3를 항상 표시.
  - `ACTIONABLE`: 현재 regime의 문턱을 통과.
  - `WATCH`: 순위는 높지만 문턱 미달.

따라서 앱은 더 이상 단순히 “추천 없음”으로 끝나지 않고, 가장 가까운 후보와 부족한 점을 계속 보여준다.

---

## 하지 않은 것

- “매일 반드시 +1%”를 보장하는 로직
- 자동 주문
- 13F를 실시간 기관매매처럼 취급
- proprietary IBD / Minervini 유료 score 복제
- 수십 개 지표를 무작정 더하는 indicator soup
- 백테스트 없이 새 feature에 과도한 가중치 부여

---

## 향후 검증 기준

각 lane은 별도 outcome을 축적해 다음을 확인해야 한다.

### Day lane
- target hit rate (+gross target before stop)
- stop-before-target rate
- MFE / MAE
- net return after assumed costs
- score bucket calibration

### Swing lane
- 5d / 10d forward return
- 5d / 10d MFE / MAE
- ACTIONABLE vs WATCH spread
- Stage-2 checks별 incremental predictive value
- 13F context가 실제로 개선했는지 ablation test

실제 기록이 충분히 쌓인 뒤에만 weight/threshold를 조정한다.
