# KRX AI Trading Assistant v5 MOBILE FREE

v4.1 FREE의 분석/학습 엔진에 **휴대폰 설치형 PWA 대시보드**와 **10분 장중 모니터링**을 추가한 버전입니다.

## 핵심 구조

- 07:45 증권사 기업리포트 수집/로컬 분석
- 08:30 장전 후보/전략 생성
- 09:10~15:20 추천 종목 **10분 간격** 현재가/VWAP/목표/손절 상태 확인
- 교체 후보 전체시장 스캔은 **30분 간격**
- 앱 화면은 켜져 있을 때 **20초 간격**으로 서버 DB를 새로 읽음
- Telegram은 상태 변화가 있거나 30분 요약 시 전송
- 15:45 추천/shadow 후보 전체 평가 + 로컬 ML 학습
- OpenAI API 없음 / 자동주문 없음

중요: iOS/Android 앱 자체의 백그라운드 실행 주기는 운영체제가 통제합니다. 따라서 **10분 모니터링은 Python 서버에서 수행**하고 휴대폰은 결과를 보는 구조입니다.

## PC에서 바로 테스트

1. 기존처럼 `.env`에 KIS/DART/Telegram 키를 설정합니다.
2. 가상환경 활성화 후:

```powershell
pip install -r requirements.txt
python run.py doctor
python run.py app
```

또는 Windows에서 `start_mobile_app.bat`를 더블클릭합니다.

3. Windows에서 PC의 Wi-Fi IP를 확인합니다.

```powershell
ipconfig
```

`IPv4 Address`가 예를 들어 `192.168.0.15`라면, 같은 Wi-Fi의 휴대폰 Safari/Chrome에서:

```text
http://192.168.0.15:8080
```

을 엽니다.

Windows 방화벽 창이 뜨면 **개인 네트워크 허용**을 선택합니다.

## iPhone 홈 화면에 설치

Safari에서 대시보드를 연 뒤:

1. 공유 버튼
2. `홈 화면에 추가`
3. 이름을 `KRX AI`로 지정
4. 추가

로컬 HTTP에서도 홈 화면 바로가기로 사용할 수 있습니다. 완전한 PWA 캐시/외부접속을 위해서는 최종 서버에서 HTTPS 사용을 권장합니다.

## PC를 꺼도 계속 감시하려면

이 프로그램을 24시간 켜진 Ubuntu/VPS에 설치하고:

```bash
python run.py app
```

을 systemd 서비스로 실행합니다. 서버에 HTTPS 주소를 붙이면 어느 곳에서든 휴대폰으로 접속할 수 있습니다.

외부 공개 시 `.env`에 반드시:

```text
APP_API_TOKEN=충분히_긴_랜덤_문자열
```

을 설정하세요. 앱 설정 화면에서 같은 토큰을 한 번 저장하면 API 요청에 사용됩니다.

## 모니터링 주기 변경

`config.yaml`:

```yaml
monitoring:
  active_interval_minutes: 10
  active_start: "09:10"
  active_end: "15:20"
  replacement_interval_minutes: 30
  telegram_summary_interval_minutes: 30
  app_refresh_seconds: 20
```

추천 종목 감시는 10분을 권장합니다. 전체시장 재탐색까지 10분마다 하면 KIS 호출량과 서버 오류 가능성이 커질 수 있어 30분으로 분리했습니다.

## 앱 화면 데이터

- 오늘 추천 종목 및 현재 상태
- 현재가 / 등락률 / VWAP
- 1·2차 진입구간
- 추격 금지 가격
- 손절/무효화 가격
- 1·2차 목표가
- 추천 비중
- 증권사 리포트 컨센서스
- 최근 추천 승률/평균수익
- 장마감 MFE/MAE 평가
- HLB 등 기존 고변동 보유 포지션 경고

## 명령어

```text
python run.py app       스케줄러 + 모바일 웹앱
python run.py web       웹앱만 실행
python run.py scheduler 스케줄러만 실행
python run.py intraday  장중 모니터링 1회 즉시 실행
python run.py status    학습 상태
```

## 보안

- KIS/DART/Telegram 비밀키는 휴대폰으로 전송하지 않습니다.
- 키는 서버의 `.env`에만 둡니다.
- 주문 API는 포함하지 않습니다.
- 인터넷 공개 시 HTTPS + APP_API_TOKEN을 사용하세요.
