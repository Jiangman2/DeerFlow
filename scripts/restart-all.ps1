$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

function Stop-Port($port) {
  Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue |
    ForEach-Object {
      try { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue } catch {}
    }
}

function Ensure-Docker() {
  try {
    docker info 1>$null 2>$null
    return
  } catch {}

  $dockerDesktop = Join-Path $env:ProgramFiles "Docker\\Docker\\Docker Desktop.exe"
  if (Test-Path $dockerDesktop) {
    Start-Process -FilePath $dockerDesktop | Out-Null
  }

  $deadline = (Get-Date).AddMinutes(3)
  while ((Get-Date) -lt $deadline) {
    try {
      docker info 1>$null 2>$null
      return
    } catch {
      Start-Sleep -Seconds 2
    }
  }

  throw "Docker engine is not running"
}

foreach ($p in @(2026, 3000, 8001, 2025)) { Stop-Port $p }

try { docker rm -f deer-flow-nginx-host 2>$null | Out-Null } catch {}

Ensure-Docker
docker compose -f (Join-Path $repoRoot "local_milvus\\milvus\\docker-compose.yml") up -d

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
Remove-Item -Force -ErrorAction SilentlyContinue ".next\\lock"
pnpm run build
if ($LASTEXITCODE -ne 0) { throw \"frontend build failed ($LASTEXITCODE)\" }
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
  "cd `"$repoRoot\\frontend`"; `$env:BETTER_AUTH_SECRET='local-dev-secret'; `$env:DEERFLOW_PROXY_SECRET='local-dev-secret'; `$env:BETTER_AUTH_BASE_URL='http://localhost:3000'; `$env:NODE_ENV='production'; `$env:PORT='3000'; node .next\\standalone\\server.js"
)

Write-Host "ready:"
Write-Host " - frontend:  http://localhost:3000"
Write-Host " - workspace: http://localhost:3000/workspace"
Write-Host " - gateway:   http://127.0.0.1:8001"
Write-Host " - langgraph: http://127.0.0.1:2025"
Write-Host " - milvus:    http://localhost:9091/healthz"
Write-Host " - minio:     http://localhost:9100/minio/health/live"
