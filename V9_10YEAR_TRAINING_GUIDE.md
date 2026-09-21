# V9 — 한국·미국 10년 사전학습

## 무엇이 추가됐나

V9는 기존 실시간 KIS/장마감 학습과 별도로 **10년 일봉 기반 사전학습 모델 4개**를 만듭니다.

- KR / day: 다음 거래일 +1% 기회 모델
- KR / swing: 다음 10거래일(약 1~2주) 모델
- US / day
- US / swing

모델은 미래 데이터를 섞지 않습니다. 오늘 종가까지의 feature만 사용하고, 정답은 다음 거래일부터 계산합니다. 마지막 약 1년은 holdout으로 남겨 과거 학습에 쓰지 않고 검증합니다.

## 데이터 소스

- 한국 종목 목록: `pykrx`로 KOSPI/KOSDAQ 현재 상장종목 검색
- 미국 종목 목록: Nasdaq Trader symbol directory에서 보통주 목록 검색
- 10년 OHLCV: `yfinance` 무료 historical daily bars

초기 구축은 무료 소스 특성상 차단/누락이 있을 수 있어 **종목별 gzip 파일 + resume** 방식입니다. 실패한 종목은 `_download_summary.json`에 남습니다.

> 중요: 현재 상장종목 목록을 과거로 소급하면 상장폐지 종목이 빠져 **생존편향**이 생깁니다. 따라서 V9의 10년 백테스트 수치를 실제 미래성과처럼 해석하면 안 됩니다. 모델 사전학습에는 활용하되, 검증은 이후 실제 일별 shadow 후보 성과와 함께 보세요.

## 설치

```bash
pip install -r requirements.txt
```

## 먼저 작은 테스트

```bash
python run.py history download KR --max-symbols 20
python run.py history build KR
```

전체 CLI는 아래 직접 실행 방식도 지원합니다.

```bash
python -m stockbot.historical_v9 download KR --years 10 --max-symbols 20
python -m stockbot.historical_v9 build KR
```

## 전체 10년 구축

한국:

```bash
python -m stockbot.historical_v9 download KR --years 10
python -m stockbot.historical_v9 build KR
```

미국:

```bash
python -m stockbot.historical_v9 download US --years 10
python -m stockbot.historical_v9 build US
```

학습:

```bash
python -m stockbot.historical_v9 train
```

생성 모델:

```text
data/models/historical_kr_day.pkl
data/models/historical_kr_swing.pkl
data/models/historical_us_day.pkl
data/models/historical_us_swing.pkl
data/models/historical_v9_training_report.json
```

## 단타 정답 정의

의사결정 시점은 `D일 종가 이후`, 진입 기준은 `D+1 시가`입니다.

- MFE: D+1 시가 대비 D+1 고가
- MAE: D+1 시가 대비 D+1 저가
- 성공: `MFE >= +1%` 이면서 `MAE > -1%`

일봉만으로 +1%와 -1%가 같은 날 모두 찍혔을 때 어느 쪽이 먼저였는지는 알 수 없습니다. 그래서 둘 다 닿은 날은 성공으로 계산하지 않는 **보수적인 label**을 사용합니다.

## 1~2주 정답 정의

D+1 시가부터 향후 10거래일을 봅니다.

- 최대상승폭(MFE) >= +4%
- 최대하락폭(MAE) > -6%

두 조건을 만족하면 swing 성공입니다. 실제 운용에 맞춰 config와 코드에서 목표값을 바꿀 수 있습니다.

## 왜 기존 장마감 학습을 없애지 않았나

10년 모델은 오래된 여러 시장 국면을 배웁니다. 반면 기존 v8.1의 장마감 learner는 **최근 시장에서 무엇이 다시 잘 맞는지**를 빠르게 반영합니다.

따라서 구조는:

```text
10년 사전학습 모델
      +
현재 실시간 점수/ML
      +
매일 장마감 적응학습
      ↓
최종 추천
```

입니다.

## 데이터 용량과 GitHub

10년 전 종목을 Git 저장소에 커밋하지 마세요. `data/historical/`은 로컬/스토리지 데이터로 관리하는 것이 좋습니다. 전체 시장 다운로드는 무료 공급자의 rate-limit에 따라 여러 번 resume 실행해야 할 수 있습니다.
