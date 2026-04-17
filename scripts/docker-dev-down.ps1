$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
docker compose -f (Join-Path $repoRoot "docker\\docker-compose-dev.yaml") down
docker compose -f (Join-Path $repoRoot "docker\\docker-compose-dev.yaml") ps

