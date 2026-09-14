# v7 SERVERLESS FREE 변경사항

## 서버 제거

- 상시 Ubuntu/VPS/Python 서버 불필요
- GitHub Actions 15분 예약 실행으로 한국/미국 장중 모니터링
- GitHub timezone schedule로 `Asia/Seoul`, `America/New_York` 각각 처리
- 미국 DST 자동 대응

## Supabase state persistence

- private bucket에 SQLite + ML 모델 + KIS/DART 캐시 state bundle 저장
- 실행 시작 시 복원, 성공 후 다시 저장
- SQLite WAL checkpoint 후 백업
- 모든 workflow에 동일 concurrency group 적용

## 모바일 앱

- Supabase public Storage에 PWA 자체 배포
- Python web server가 없어도 휴대폰에서 실행
- dashboard/history JSON만 public 제공
- 개인 보유 포지션/리스크 설정은 public feed에서 제거
- 앱 데이터 refresh 60초, 분석 자체는 15분 주기

## Gold 오류 수정

- 제거된 FRED `GOLDAMGBD228NLBM` 삭제
- `KCROROG` (Gold & USD risk-on/off)로 대체
- 실시간 금 가격 proxy는 KIS `GLD` 유지

## 무료 모드

- OpenAI 없음
- 자동주문 없음
- KIS + DART + FRED + 증권사 리포트 + local ML
