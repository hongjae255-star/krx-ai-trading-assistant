# v4 FREE 변경사항

- OpenAI Python SDK 제거 (`requirements.txt`에서 삭제)
- OpenAI 클라이언트/Responses/Web Search 코드 제거
- `FREE_MODE=true` 기본값
- `LocalAnalyst` 추가: DART + 공개 뉴스 제목 + 증권사 리포트를 규칙/통계로 분석
- `NaverFinanceNewsClient` 추가: 종목별 공개 뉴스 목록 제목 메타데이터 best-effort 수집
- 리포트 분류를 100% 로컬 규칙 기반으로 변경
- 신규 ML feature: `event_positive_strength`, `event_risk_inverse`
- 유증/CB/거래정지/상폐/횡령/배임 등 위험 이벤트를 risk flag로 자동 변환
- `doctor`에서 OpenAI를 실패로 보지 않고 `DISABLED (Free Mode)`로 표시
- `status`에 `FREE_LOCAL_RULE_ML` 모드 표시
- 기존 v3의 frozen snapshot, shadow 후보 전체 사후평가, walk-forward/embargo, 확률 calibration, MFE/MAE 예측 유지
- 실제 주문 기능 없음
