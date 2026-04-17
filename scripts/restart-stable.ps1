$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

function Stop-Port($port) {
  Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue |
    ForEach-Object {
      try { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue } catch {}
    }
}

Stop-Port 2025
Stop-Port 8001
Stop-Port 3000

try { docker rm -f deer-flow-nginx-host 2>$null | Out-Null } catch {}

Start-Process -WindowStyle Minimized -FilePath powershell -ArgumentList @(
  "-NoProfile",
  "-ExecutionPolicy", "Bypass",
  "-Command",
  "cd `"$repoRoot\\backend`"; `$env:PYTHONPATH='.'; `$env:NO_COLOR='1'; uv run langgraph dev --no-browser --allow-blocking --port 2025 --no-reload"
)

Start-Process -WindowStyle Minimized -FilePath powershell -ArgumentList @(
  "-NoProfile",
  "-ExecutionPolicy", "Bypass",
  "-Command",
  "cd `"$repoRoot\\backend`"; `$env:PYTHONPATH='.'; `$env:DEERFLOW_PROXY_SECRET='local-dev-secret'; uv run uvicorn app.gateway.app:app --host 0.0.0.0 --port 8001"
)

Push-Location (Join-Path $repoRoot "frontend")
$env:BETTER_AUTH_SECRET = "local-dev-secret"
try {
  $procs = Get-CimInstance Win32_Process | Where-Object { $_.Name -match "node" -and $_.CommandLine -match "next(\\.exe)?\\s+build|next\\\\dist\\\\bin\\\\next.*build" }
  foreach ($p in $procs) {
    try { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue } catch {}
  }
} catch {}
Remove-Item -Force -ErrorAction SilentlyContinue ".next\\lock"
pnpm run build
if ($LASTEXITCODE -ne 0) { throw "frontend build failed ($LASTEXITCODE)" }
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue ".next\\standalone\\.next\\static"
New-Item -ItemType Directory -Force -Path ".next\\standalone\\.next\\static" | Out-Null
Copy-Item -Recurse -Force ".next\\static\\*" ".next\\standalone\\.next\\static\\"
if (Test-Path "public") {
  Remove-Item -Recurse -Force -ErrorAction SilentlyContinue ".next\\standalone\\public"
  Copy-Item -Recurse -Force "public" ".next\\standalone\\public"
}
Pop-Location

Start-Process -WindowStyle Minimized -FilePath powershell -ArgumentList @(
  "-NoProfile",
  "-ExecutionPolicy", "Bypass",
  "-Command",
  "cd `"$repoRoot\\frontend`"; `$env:BETTER_AUTH_SECRET='local-dev-secret'; `$env:DEERFLOW_PROXY_SECRET='local-dev-secret'; `$env:NODE_ENV='production'; `$env:PORT='3000'; node .next\\standalone\\server.js"
)

Start-Sleep -Seconds 2
& (Join-Path $repoRoot "scripts\\nginx-host-up.ps1")

Write-Host "ready:"
Write-Host " - http://localhost:2026"
Write-Host " - langgraph: http://127.0.0.1:2025"
Write-Host " - gateway:   http://127.0.0.1:8001"
Write-Host " - frontend:  http://127.0.0.1:3000"
