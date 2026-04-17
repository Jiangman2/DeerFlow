$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$conf = Join-Path $repoRoot "docker\\nginx\\nginx.host.conf"

if (-not (Test-Path $conf)) {
  throw "Missing nginx config: $conf"
}

try {
  docker rm -f deer-flow-nginx-host 2>$null | Out-Null
} catch {
}

docker run -d `
  --name deer-flow-nginx-host `
  -p 2026:2026 `
  -v "${conf}:/etc/nginx/nginx.conf:ro" `
  nginx:alpine | Out-Null

Write-Host "nginx ready on http://localhost:2026"
