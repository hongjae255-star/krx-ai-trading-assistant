param(
  [Parameter(Mandatory=$true)]
  [string]$TargetRepo
)

$ErrorActionPreference = 'Stop'
$Source = Split-Path -Parent $MyInvocation.MyCommand.Path
$Target = (Resolve-Path $TargetRepo).Path

if (-not (Test-Path (Join-Path $Target '.git'))) {
  throw "TargetRepo is not a Git repository: $Target"
}

Write-Host "Source : $Source"
Write-Host "Target : $Target"
Write-Host "Copying v7.8 while preserving .git, .env, .venv and runtime data..."

$null = robocopy $Source $Target /E /R:2 /W:1 `
  /XD .git .venv __pycache__ .pytest_cache data `
  /XF .env
$code = $LASTEXITCODE
if ($code -ge 8) {
  throw "robocopy failed with code $code"
}

Set-Location $Target
Write-Host "`nGit status:"
git status --short
Write-Host "`nNext commands:"
Write-Host "  git add -A"
Write-Host '  git commit -m "Upgrade to v7.8 macro resilience and manual refresh"'
Write-Host "  git push origin main"
