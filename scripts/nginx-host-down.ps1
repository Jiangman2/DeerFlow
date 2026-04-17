$ErrorActionPreference = "Continue"
try {
  docker rm -f deer-flow-nginx-host 2>$null | Out-Null
} catch {
}
Write-Host "stopped deer-flow-nginx-host"
