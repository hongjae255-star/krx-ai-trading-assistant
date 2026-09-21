# v7.9 → v8.0 업그레이드

v8.0은 v7.7 / v7.8 / v7.9 기능을 모두 포함하는 누적 버전입니다.

## 1. ZIP 압축 해제

예:

```text
C:\Users\nohon\Downloads\krx_ai_trading_assistant_v8_0_dual_strategy_free
```

## 2. 기존 clone에 복사

v8.0 폴더에서 PowerShell:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\upgrade_to_v8_0.ps1 -TargetRepo "C:\Users\nohon\Downloads\krx-ai-v75"
```

## 3. Git 확인/Push

```powershell
cd "C:\Users\nohon\Downloads\krx-ai-v75"
git status
git add -A
git commit -m "Upgrade to v8.0 dual strategy"
git push origin main
```

## 4. SEC 13F용 Secret 추가

GitHub repository → Settings → Secrets and variables → Actions → New repository secret

Name:

```text
SEC_USER_AGENT
```

Value 예:

```text
hongjae255 stockbot your-email@example.com
```

실제로 연락 가능한 본인 이메일을 사용하세요. Secret 값은 ChatGPT에 보내지 마세요.

이 Secret이 없어도 한국/미국 종목 lane은 작동하지만 미국 13F는 `setup_needed`로 표시됩니다.

## 5. 최초 생성

GitHub Actions에서 순서대로 한 번 실행:

```text
Cloud manual job → kr-premarket
Cloud manual job → us-premarket
Cloud manual job → publish
```

미국 13F만 따로 테스트하려면:

```text
Cloud manual job → institutional
```

## 6. 확인

앱에서 한국/미국 각각 다음이 보여야 합니다.

- 하루 +1% 목표: Top 3 (ACTIONABLE 또는 WATCH)
- 1–2주 Swing: Top 3 (ACTIONABLE 또는 WATCH)
- 엄격 실행 전략: 기준 통과 종목이 없으면 0개 가능
- 미국 탭: 유명 투자회사 13F

`WATCH`는 매수 지시가 아니라 기준에 가장 가까운 관찰 후보입니다.
