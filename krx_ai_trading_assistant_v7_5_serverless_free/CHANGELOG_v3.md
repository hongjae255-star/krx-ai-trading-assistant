# CHANGELOG v3

## 예측력/검증 구조 개선

- 전체 장전 후보군을 `candidate_snapshots`에 최초 1회 동결하고 최종 추천 여부와 무관하게 장마감 평가.
- 학습 데이터는 `as_of_date` 이전 날짜만 허용.
- 거래일 단위 expanding walk-forward + embargo 도입.
- LogisticRegression + HistGradientBoostingClassifier + RandomForestClassifier 확률 앙상블.
- Ridge + HistGradientBoostingRegressor + RandomForestRegressor 기대수익 앙상블.
- OOF probability calibration(sigmoid/isotonic)과 calibration error/bin 통계 추가.
- OOF의 앞 구간은 calibration/weight 학습, 뒤 구간은 untouched challenger validation으로 사용.
- 기존 heuristic baseline과 champion/challenger 비교 후 검증 우위가 있을 때만 ML을 추천점수에 혼합.
- ML 최대 blend 65%, drift 시 50% 축소 또는 완전 비활성화.
- 경험적 OOF residual prediction interval 추가.
- 비용 차감 수익률을 classification/regression target으로 사용.
- recency sample weighting 추가.
- market breadth/dispersion/risk-on/risk-off/high-vol regime feature 추가.
- 후보군 내 cross-sectional rank feature 추가.
- 10일 momentum, 변동성 품질, ATR%, 20일 range/high proximity, close location, volume pressure 추가.
- `global_risk_on`, `macro_event_safety` 추가. 아직 발표되지 않은 경제지표 결과를 미래정보로 사용하지 않도록 AI prompt 강화.
- 확률/기대수익/하방구간이 기준 미달이면 최종 종목 수를 강제로 채우지 않는 abstention/cash 모드 추가.
- `validate-model` 명령 추가.
- 장마감에 전체 shadow 후보의 예측 정확도, Brier, 수익률 MAE, Rank-IC, interval coverage 기록.

## 안정성 수정

- v2의 일부 nested config가 일반 dict의 dotted key 조회로 기본값에 폴백할 수 있던 부분을 `Settings.get()` 기반으로 수정.
- 장전 추천/후보 snapshot 저장을 overwrite가 아닌 최초 기록 보존 방식으로 변경.
- Asia/Seoul 기준 날짜를 명시적으로 사용.
- 리포트 후보 주입 상한을 낮춰 리포트가 많은 종목이 시장 주도주 후보 풀을 과도하게 점유하지 않도록 조정.

## 테스트

- 기존 테스트 + 날짜 누수 방지 split, snapshot immutability, nonlinear synthetic ensemble, cross-sectional features 검증.
- 현재 총 14개 테스트 통과.

## 자동매매

- 없음. 주문 API/주문 endpoint를 포함하지 않습니다.

## v3.0.1 - 2026-09-10

- Fixed a predictor training bug caused by reusing the variable name `mae` for both the realized maximum-adverse-excursion target array and the scalar mean-absolute-error metric.
- Renamed the intraday downside target to `mae_target` and the return regression validation metric to `return_mae`.
- Confirmed final MFE/MAE regressors train on full target arrays rather than a scalar validation metric.
- Validation: `14 passed` with pytest, full module byte-compilation passed, CLI smoke test passed.
- Re-checked that no KIS order endpoint or automatic order placement function is present.
