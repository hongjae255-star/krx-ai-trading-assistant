# v3 Predictive Model Design

## 목표

v3의 목표는 “백테스트 숫자를 가장 높이는 모델”이 아니라 **08:30에 실제로 알 수 있었던 정보만 사용해 다음 거래일에도 재현 가능한 예측력을 최대화**하는 것입니다. 자동주문은 없습니다.

## 핵심 변경

1. **Frozen shadow universe**: 최종 추천 2~3개뿐 아니라 장전 후보 풀 전체(기본 24개)를 최초 1회 저장하고 모두 장마감 평가합니다.
2. **Two targets**: 거래비용 차감 상승확률과 시가→종가 순수익률을 별도 모델이 예측합니다.
3. **Linear + nonlinear ensemble**: Logistic/Ridge와 HistGradientBoosting/RandomForest를 결합합니다.
4. **Date-grouped walk-forward**: 같은 날짜 종목을 쪼개지 않고 과거→미래 순서만 허용합니다.
5. **Embargo**: test 직전 날짜를 기본 1일 학습에서 제외합니다.
6. **Calibration holdout**: OOF 앞 구간으로 calibration/ensemble weight를 만들고, 뒤 OOF 구간에서 challenger 성능을 최종 판정합니다.
7. **Champion/challenger**: 기존 heuristic baseline보다 개선되지 않으면 ML은 실제 추천 점수에 들어가지 않습니다.
8. **Selective prediction**: 좋은 확률/기대수익/하방구간이 아니면 현금 대기를 허용합니다.
9. **Regime & drift**: risk-on/off/high-vol 및 feature distribution drift가 크면 ML 비중을 축소합니다.
10. **Cost-aware labels**: 기본 왕복 비용 25bp를 차감한 뒤 label/reward를 만듭니다.
11. **Broker report signal**: 리포트 방향, 추정치 변화, 목표가 괴리, 신선도, 복수 증권사 합의, 과거 단기 적중도를 feature로 사용합니다.
12. **Independent plan evaluation**: 종목선정 성과와 진입/손절/목표가 설계 성과를 분리합니다.

## 예측 대상

- Classification: `net_open_to_close_pct > label_threshold_pct`
- Regression: `net_open_to_close_pct`

기본 `estimated_round_trip_cost_bps=25`, `label_threshold_pct=0.30`입니다. 실제 증권사 수수료와 예상 슬리피지에 맞게 바꾸는 것이 좋습니다.

## 검증 지표

단순 directional accuracy 하나만 최적화하지 않습니다.

- Brier score: 확률의 정확성과 calibration
- Expected Calibration Error
- ROC-AUC / Log loss
- Return MAE / RMSE
- Daily cross-sectional Rank-IC
- Prediction interval empirical coverage
- 실제 shadow universe의 일별 평균 순수익

Accuracy만 높이고 매수 확률을 전부 51%로 만드는 모델, 또는 방향은 잘 맞지만 기대수익 순위가 틀리는 모델을 구별하기 위한 구성입니다.

## 확률 calibration

OOF calibration 표본이 작으면 sigmoid(Logistic regression) calibration을 사용합니다. 충분히 큰 표본에서는 isotonic regression을 허용합니다. calibration에 사용한 OOF 구간과 challenger 평가 OOF 구간을 시간순으로 분리합니다.

## 과적합 방지

- 실시간 장전 snapshot 덮어쓰기 금지
- `as_of_date` 이후 데이터 학습 금지
- random split 금지
- whole-date grouping
- embargo
- minimum sample / minimum trading dates
- regularized linear models + shallow trees
- recent-data sample weighting
- drift gate
- baseline challenger gate
- ML blend maximum 65%

## 데이터가 쌓이는 동안

v2 데이터에는 전체 shadow universe의 동결 feature가 없으므로 v3 supervised model은 즉시 활성화되지 않을 수 있습니다. 기본 24후보/일이면 약 20거래일 후 400개 이상의 clean sample이 생깁니다. 그 전에는 기존 heuristic + 리포트/뉴스/수급 로직으로 동작하고, 모든 후보의 결과를 계속 축적합니다.

## 왜 딥 강화학습을 기본으로 쓰지 않았나

하루 20~30개의 단면 후보, 수십 거래일 수준에서는 state/action/reward 공간에 비해 독립 표본이 매우 적습니다. DQN/PPO 같은 모델은 쉽게 시장 잡음을 외울 수 있습니다. 따라서 현재는 검증 가능한 supervised ensemble + conservative online baseline learning을 사용하고, 충분한 데이터가 쌓인 이후 regime-specific model이나 contextual bandit을 challenger로 추가하는 편이 안전합니다.

## 운영 시 권장 원칙

- `python run.py validate-model`의 validation 결과가 나빠지면 설정을 억지로 완화하지 않습니다.
- 실거래 여부와 무관하게 추천/후보 전체를 계속 평가합니다.
- 비용/슬리피지 가정을 실제 계좌에 맞춥니다.
- 모델이 `active=false`면 실패가 아니라 안전장치가 작동한 것입니다.
- 한 달 성과보다 여러 시장 국면을 포함한 누적 walk-forward 결과를 더 중요하게 봅니다.
