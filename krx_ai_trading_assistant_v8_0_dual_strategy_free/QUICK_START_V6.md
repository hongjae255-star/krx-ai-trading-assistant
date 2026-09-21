# v6 휴대폰 + 한국/미국 빠른 시작

## 1) 설치
```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\install_windows.ps1
```

## 2) .env 입력
```text
FREE_MODE=true
KIS_APP_KEY=...
KIS_APP_SECRET=...
DART_API_KEY=...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
FRED_API_KEY=
```
FRED 키는 없어도 실행됩니다. 무료 키를 발급받아 넣으면 vintage/as-of 기능을 사용할 수 있습니다.

## 3) 진단
```powershell
.\.venv\Scripts\python.exe run.py doctor
```
`KIS`와 `KIS_US`가 True인지 확인합니다.

## 4) 글로벌 데이터만 확인
```powershell
.\.venv\Scripts\python.exe run.py macro
```

## 5) 미국장만 시험
```powershell
.\.venv\Scripts\python.exe run.py us-premarket
.\.venv\Scripts\python.exe run.py us-intraday
```
미국 정규장 시간 밖에서는 현재가/분봉 응답 내용이 제한될 수 있습니다.

## 6) 앱 + 전체 스케줄러
```powershell
.\.venv\Scripts\python.exe run.py app
```

## 7) iPhone
`ipconfig`에서 PC IPv4 확인 → iPhone과 같은 Wi-Fi → Safari에서 `http://IPv4:8080` → 공유 → 홈 화면에 추가.

앱 상단 🇰🇷 한국 / 🇺🇸 미국 버튼으로 시장을 바꿉니다.
