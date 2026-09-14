$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    Write-Host "Python launcher(py)를 찾을 수 없습니다. Python 3.11을 먼저 설치하세요." -ForegroundColor Red
    exit 1
}

if (-not (Test-Path ".venv")) {
    py -3.11 -m venv .venv
}
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\pip.exe install -r requirements.txt

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "`.env 파일을 만들었습니다. API 키를 입력하세요." -ForegroundColor Yellow
}

Write-Host "설치 완료." -ForegroundColor Green
Write-Host "1) .env 입력"
Write-Host "2) .venv\Scripts\python.exe run.py doctor"
Write-Host "3) start_mobile_app.bat"
