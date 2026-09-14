# v7 Serverless Free — 설치 가이드

이 버전은 24시간 켜진 PC/VPS 없이 동작합니다.

- **GitHub Actions**: 한국/미국 시장 분석을 15분 주기로 실행
- **Supabase private Storage**: SQLite DB, ML 모델, KIS 캐시를 압축 보관
- **Supabase public Storage**: 개인정보를 제거한 dashboard/history JSON + 설치형 PWA 제공
- **Telegram**: 중요한 신호 알림
- **OpenAI**: 사용하지 않음
- **자동주문**: 없음

## 1. Supabase 프로젝트 만들기

Supabase에서 무료 프로젝트를 하나 만듭니다. `Connect` 또는 API Keys 화면에서 다음 두 값을 확인합니다.

- Project URL: `https://xxxx.supabase.co`
- Secret key: `sb_secret_...`

> Secret key는 GitHub Secrets에만 넣고 소스코드, 브라우저, 휴대폰 앱에는 절대 넣지 마세요.

Storage bucket은 직접 만들 필요가 없습니다. 첫 cloud job/PWA deploy가 다음 두 bucket을 자동 생성합니다.

- `krx-ai-state` — **private**
- `krx-ai-public` — **public**

## 2. GitHub private repository 만들기

1. GitHub → New repository
2. 이름 예: `krx-ai-trading-assistant`
3. **Private 또는 Public** 선택
   - 개인 설정은 Secrets로 분리되어 있어 public repo도 가능
   - 완전 무료 Actions 사용량을 우선하면 public repo가 유리
4. 이 ZIP의 파일을 repository root에 업로드/push
5. 기본 branch는 `main`

`.env` 파일은 GitHub에 올리지 마세요. `.gitignore`에 이미 제외되어 있습니다.

## 3. GitHub Secrets 등록

Repository → **Settings → Secrets and variables → Actions → New repository secret**

다음을 등록합니다.

필수:

- `KIS_APP_KEY`
- `KIS_APP_SECRET`
- `DART_API_KEY`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `SUPABASE_URL`
- `SUPABASE_SECRET_KEY`

개인 설정(권장, 특히 public repo 사용 시):

- `EXISTING_POSITIONS_JSON` 예: `[{"code":"028300","name":"HLB","quantity":221,"average_price":52800,"risk_class":"high"}]`
- `DAY_TRADE_CAPITAL_KRW` 예: `5000000`

선택:

- `FRED_API_KEY`

FRED key가 없으면 공개 CSV fallback을 사용합니다.

## 4. 최초 cloud state 생성

GitHub → **Actions → Cloud manual job → Run workflow**

처음에는:

- job: `macro`
- full_scan: 아무 값이나 무관

으로 실행합니다.

정상 완료되면 Supabase Storage에:

- private `krx-ai-state/state/state_bundle.zip`
- public `krx-ai-public/dashboard.json`
- public `krx-ai-public/history.json`

이 생깁니다.

## 5. 모바일 PWA 배포

GitHub → **Actions → Deploy mobile PWA to Supabase → Run workflow**

완료 로그 마지막에 다음 형태의 URL이 출력됩니다.

`https://<project>.supabase.co/storage/v1/object/public/krx-ai-public/app/index.html`

이 주소를 휴대폰 Safari/Chrome으로 엽니다.

### iPhone

Safari → 공유 → **홈 화면에 추가**

### Android

Chrome → 메뉴 → **앱 설치 / 홈 화면에 추가**

## 6. 자동 스케줄

GitHub Actions가 자동으로 실행합니다.

### 한국시장 (Asia/Seoul)

- 07:47 리포트 분석
- 08:32 장전 분석
- 09:12~15:27 15분 모니터링
- 30분 간격은 전체시장 신규 주도주 재스캔 포함
- 15:47 장마감 평가/학습

### 미국시장 (America/New_York)

GitHub Actions timezone 기능을 사용하므로 DST가 자동 반영됩니다.

- 09:17 ET 장전 분석
- 09:42~15:57 ET 15분 모니터링
- 30분 간격 전체시장 재스캔
- 16:17 ET 장마감 평가/학습

스케줄 작업은 GitHub 인프라 상황에 따라 몇 분 지연될 수 있으므로 초단타/HFT 용도로 사용하면 안 됩니다.

## 7. 상태가 보존되는 방식

GitHub Actions runner는 매 실행마다 새 컴퓨터입니다. v7은 이를 다음 방식으로 해결합니다.

1. `krx-ai-state/state/state_bundle.zip` 다운로드
2. SQLite/ML 모델 복원
3. 분석 실행
4. SQLite WAL checkpoint
5. 새 state bundle 업로드
6. dashboard/history JSON 갱신

모든 workflow는 동일한 GitHub Actions `concurrency` group을 사용하므로 한국/미국 job이 동시에 state를 덮어쓰지 않습니다.

## 8. 금(Gold) 404 수정

v6의 FRED `GOLDAMGBD228NLBM`은 폐기된 시계열이라 404가 발생했습니다.

v7:

- 폐기된 series 제거
- FRED `KCROROG` = Gold & USD risk-on/off component 사용
- 실제 장중 금 가격/수급 = KIS 해외주식 `GLD` 사용

따라서 기존 gold 404 경고는 발생하지 않습니다.

## 9. 수동 실행

Actions → **Cloud manual job**에서 필요할 때 직접 실행할 수 있습니다.

- `publish`
- `macro`
- `research`
- `kr-premarket`
- `kr-intraday`
- `kr-close`
- `us-premarket`
- `us-intraday`
- `us-close`

## 10. 로컬 PC 방식도 유지

기존처럼 PC에서 실행하는 것도 가능합니다.

```powershell
python run.py doctor
python run.py app
```

Supabase bucket 초기화/PWA 배포를 PC에서 직접 하고 싶다면:

```powershell
python run.py cloud-init
python run.py cloud-deploy
```

이 경우 `.env`에 `SUPABASE_URL`, `SUPABASE_SECRET_KEY`가 있어야 합니다.

## 개인정보 / 보안

public dashboard에는 다음을 게시하지 않습니다.

- 기존 보유수량/평단
- 단타 운용자금/risk 설정
- KIS token
- API keys
- 전체 SQLite DB

private state bucket에만 DB와 ML state가 저장됩니다.
