param(
  [Parameter(Mandatory=$true)]
  [string]$TargetRepo
)

$Source = Split-Path -Parent $MyInvocation.MyCommand.Path
$Target = (Resolve-Path $TargetRepo).Path

Write-Host "v8.0 source: $Source"
Write-Host "target repo: $Target"

# Preserve local git metadata, virtual env, runtime state and .env secrets.
robocopy $Source $Target /E /R:2 /W:1 /XD .git .venv __pycache__ data /XF .env | Out-Host
$rc = $LASTEXITCODE
if ($rc -ge 8) {
  throw "robocopy failed with exit code $rc"
}

Write-Host "Upgrade copy complete. Next: git status"
