$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$uv = Join-Path $env:USERPROFILE ".local\\bin\\uv.exe"
if (-not (Test-Path $uv)) {
  $uv = "uv"
}
$logDir = Join-Path $repoRoot ".logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

function Stop-Port($port) {
  Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue |
    ForEach-Object {
      try { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue } catch {}
    }
}

Stop-Port 2025
Stop-Port 8001
Stop-Port 3000
Stop-Port 2026

try { docker rm -f deer-flow-nginx-host 2>$null | Out-Null } catch {}

Start-Process -WindowStyle Minimized -FilePath powershell -RedirectStandardOutput (Join-Path $logDir "langgraph.out.log") -RedirectStandardError (Join-Path $logDir "langgraph.err.log") -ArgumentList @(
  "-NoProfile",
  "-ExecutionPolicy", "Bypass",
  "-Command",
  "cd `"$repoRoot\\backend`"; `$env:PYTHONPATH='.'; `$env:NO_COLOR='1'; & `"$uv`" run langgraph dev --no-browser --allow-blocking --port 2025 --no-reload"
)

Start-Process -WindowStyle Minimized -FilePath powershell -RedirectStandardOutput (Join-Path $logDir "gateway.out.log") -RedirectStandardError (Join-Path $logDir "gateway.err.log") -ArgumentList @(
  "-NoProfile",
  "-ExecutionPolicy", "Bypass",
  "-Command",
  "cd `"$repoRoot\\backend`"; `$env:PYTHONPATH='.'; `$env:DEERFLOW_PROXY_SECRET='local-dev-secret'; & `"$uv`" run uvicorn app.gateway.app:app --host 0.0.0.0 --port 8001"
)

Start-Process -WindowStyle Minimized -FilePath powershell -RedirectStandardOutput (Join-Path $logDir "frontend.out.log") -RedirectStandardError (Join-Path $logDir "frontend.err.log") -ArgumentList @(
  "-NoProfile",
  "-ExecutionPolicy", "Bypass",
  "-Command",
  "cd `"$repoRoot\\frontend`"; `$env:BETTER_AUTH_SECRET='local-dev-secret'; `$env:DEERFLOW_PROXY_SECRET='local-dev-secret'; `$env:BETTER_AUTH_BASE_URL='http://localhost:3000'; pnpm run dev -- --hostname 0.0.0.0 --port 3000"
)

Write-Host "ready:"
Write-Host " - frontend:  http://localhost:3000"
Write-Host " - gateway:   http://127.0.0.1:8001"
Write-Host " - langgraph: http://127.0.0.1:2025"
