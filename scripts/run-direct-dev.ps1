$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$logDir = Join-Path $repoRoot ".logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$uv = Join-Path $env:USERPROFILE ".local\\bin\\uv.exe"
if (-not (Test-Path $uv)) {
  $uv = "uv"
}

$langgraphOut = Join-Path $logDir "langgraph.out.log"
$langgraphErr = Join-Path $logDir "langgraph.err.log"
$gatewayOut = Join-Path $logDir "gateway.out.log"
$gatewayErr = Join-Path $logDir "gateway.err.log"
$frontendOut = Join-Path $logDir "frontend.out.log"
$frontendErr = Join-Path $logDir "frontend.err.log"

Remove-Item -Force -ErrorAction SilentlyContinue $langgraphOut, $langgraphErr, $gatewayOut, $gatewayErr, $frontendOut, $frontendErr

function Load-DotEnv($path) {
  $vars = @{}
  if (-not (Test-Path $path)) { return $vars }
  foreach ($line in Get-Content -LiteralPath $path -ErrorAction SilentlyContinue) {
    if ($null -eq $line) { continue }
    $t = ($line.ToString()).Trim()
    if ($t.Length -eq 0) { continue }
    if ($t.StartsWith("#")) { continue }
    $idx = $t.IndexOf("=")
    if ($idx -lt 1) { continue }
    $k = $t.Substring(0, $idx).Trim()
    $v = $t.Substring($idx + 1).Trim()
    if ($k.Length -eq 0) { continue }
    $vars[$k] = $v
  }
  return $vars
}

$rootEnv = Load-DotEnv (Join-Path $repoRoot ".env")
$devSecret = $rootEnv["DEERFLOW_PROXY_SECRET"]
if (-not $devSecret) { $devSecret = "local-dev-secret" }
$betterAuthSecret = $rootEnv["BETTER_AUTH_SECRET"]
if (-not $betterAuthSecret) { $betterAuthSecret = "local-dev-secret-local-dev-secret-123456" }
$noProxy = "localhost,127.0.0.1,::1,hecc.ai.cnooc"
$disableSystemProxy = ($env:DEERFLOW_DISABLE_SYSTEM_PROXY -eq "1")
$allowPublicModels = ($env:DEERFLOW_ALLOW_PUBLIC_MODELS -eq "1")

function Get-SystemProxy() {
  $result = @{
    Http  = $null
    Https = $null
  }
  try {
    $enable = (Get-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings" -Name ProxyEnable -ErrorAction Stop).ProxyEnable
    if ($enable -ne 1) { return $result }
    $raw = (Get-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings" -Name ProxyServer -ErrorAction Stop).ProxyServer
    if (-not $raw) { return $result }

    if ($raw -match "http=" -or $raw -match "https=") {
      foreach ($part in $raw.Split(";")) {
        $p = $part.Trim()
        if ($p.StartsWith("http=")) { $result.Http = $p.Substring(5) }
        elseif ($p.StartsWith("https=")) { $result.Https = $p.Substring(6) }
      }
    } else {
      $result.Http = $raw
      $result.Https = $raw
    }
  } catch {
    return $result
  }
  return $result
}

$sysProxy = Get-SystemProxy
$sysHttpProxy = $sysProxy.Http
$sysHttpsProxy = $sysProxy.Https
if ($sysHttpProxy -and -not $sysHttpProxy.Contains("://")) { $sysHttpProxy = "http://$sysHttpProxy" }
if ($sysHttpsProxy -and -not $sysHttpsProxy.Contains("://")) { $sysHttpsProxy = "http://$sysHttpsProxy" }

function Stop-Port($port) {
  Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue |
    ForEach-Object {
      try { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue } catch {}
    }
}

function Wait-Listen($port, $seconds) {
  $deadline = (Get-Date).AddSeconds($seconds)
  while ((Get-Date) -lt $deadline) {
    $c = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -ne $c) { return $true }
    Start-Sleep -Milliseconds 200
  }
  return $false
}

Stop-Port 2025
Stop-Port 8001
Stop-Port 3000

Push-Location (Join-Path $repoRoot "backend")
$env:PYTHONPATH = "."
$env:NO_COLOR = "1"
$backendCwd = (Get-Location).Path
$deerFlowHome = (Join-Path $backendCwd ".deer-flow")
$configPath = (Join-Path $repoRoot "config.yaml")
$extensionsPath = (Join-Path $repoRoot "extensions_config.json")
$langgraphJob = Start-Job -Name "langgraph" -ScriptBlock {
  param($cwd, $out, $uvExe, $configPath, $extensionsPath, $deerFlowHome, $envVars, $sysHttpProxy, $sysHttpsProxy, $noProxy, $disableSystemProxy)
  Set-Location $cwd
  $env:PYTHONPATH = "."
  $env:NO_COLOR = "1"
  foreach ($k in $envVars.Keys) { Set-Item -Path ("Env:{0}" -f $k) -Value $envVars[$k] }
  Remove-Item Env:ALL_PROXY -ErrorAction SilentlyContinue
  Remove-Item Env:all_proxy -ErrorAction SilentlyContinue
  $env:DEER_FLOW_HOME = $deerFlowHome
  $env:DEER_FLOW_CONFIG_PATH = $configPath
  $env:DEER_FLOW_EXTENSIONS_CONFIG_PATH = $extensionsPath
  if (-not $disableSystemProxy) {
    if (-not $env:HTTP_PROXY -and $sysHttpProxy) { $env:HTTP_PROXY = $sysHttpProxy }
    if (-not $env:http_proxy -and $sysHttpProxy) { $env:http_proxy = $sysHttpProxy }
    if (-not $env:HTTPS_PROXY -and $sysHttpsProxy) { $env:HTTPS_PROXY = $sysHttpsProxy }
    if (-not $env:https_proxy -and $sysHttpsProxy) { $env:https_proxy = $sysHttpsProxy }
  }
  Set-Item -Path "Env:NO_PROXY" -Value $noProxy
  Set-Item -Path "Env:no_proxy" -Value $noProxy
  $cmd = "`"$uvExe`" run langgraph dev --no-browser --allow-blocking --port 2025 --no-reload"
  cmd /c "$cmd 2>&1" | Out-File -FilePath $out -Append -Encoding utf8
} -ArgumentList @($backendCwd, $langgraphOut, $uv, $configPath, $extensionsPath, $deerFlowHome, $rootEnv, $sysHttpProxy, $sysHttpsProxy, $noProxy, $disableSystemProxy)

$gatewayJob = Start-Job -Name "gateway" -ScriptBlock {
  param($cwd, $out, $uvExe, $configPath, $extensionsPath, $deerFlowHome, $proxySecret, $envVars, $sysHttpProxy, $sysHttpsProxy, $noProxy, $disableSystemProxy)
  Set-Location $cwd
  $env:PYTHONPATH = "."
  foreach ($k in $envVars.Keys) { Set-Item -Path ("Env:{0}" -f $k) -Value $envVars[$k] }
  Remove-Item Env:ALL_PROXY -ErrorAction SilentlyContinue
  Remove-Item Env:all_proxy -ErrorAction SilentlyContinue
  $env:DEER_FLOW_HOME = $deerFlowHome
  $env:DEER_FLOW_CONFIG_PATH = $configPath
  $env:DEER_FLOW_EXTENSIONS_CONFIG_PATH = $extensionsPath
  $env:DEERFLOW_PROXY_SECRET = $proxySecret
  $env:LANGGRAPH_BASE_URL = "http://127.0.0.1:2025"
  if (-not $disableSystemProxy) {
    if (-not $env:HTTP_PROXY -and $sysHttpProxy) { $env:HTTP_PROXY = $sysHttpProxy }
    if (-not $env:http_proxy -and $sysHttpProxy) { $env:http_proxy = $sysHttpProxy }
    if (-not $env:HTTPS_PROXY -and $sysHttpsProxy) { $env:HTTPS_PROXY = $sysHttpsProxy }
    if (-not $env:https_proxy -and $sysHttpsProxy) { $env:https_proxy = $sysHttpsProxy }
  }
  Set-Item -Path "Env:NO_PROXY" -Value $noProxy
  Set-Item -Path "Env:no_proxy" -Value $noProxy
  $cmd = "`"$uvExe`" run uvicorn app.gateway.app:app --host 0.0.0.0 --port 8001"
  cmd /c "$cmd 2>&1" | Out-File -FilePath $out -Append -Encoding utf8
} -ArgumentList @($backendCwd, $gatewayOut, $uv, $configPath, $extensionsPath, $deerFlowHome, $devSecret, $rootEnv, $sysHttpProxy, $sysHttpsProxy, $noProxy, $disableSystemProxy)
Pop-Location

Push-Location (Join-Path $repoRoot "frontend")
$frontendCwd = (Get-Location).Path
$frontendDbDir = (Join-Path $frontendCwd ".deer-flow")
New-Item -ItemType Directory -Force -Path $frontendDbDir | Out-Null
$env:BETTER_AUTH_SECRET = $betterAuthSecret
$env:BETTER_AUTH_URL = "http://localhost:3000"
$env:BETTER_AUTH_BASE_URL = "http://localhost:3000"
$env:BETTER_AUTH_DB_DIR = $frontendDbDir
$env:DEERFLOW_GATEWAY_BASE_URL = "http://127.0.0.1:8001"
$frontendJob = Start-Job -Name "frontend" -ScriptBlock {
  param($cwd, $out, $authSecret, $proxySecret, $dbDir, $envVars, $allowPublicModels)
  Set-Location $cwd
  $env:NODE_NO_WARNINGS = "1"
  $nodeOptions = "--no-warnings"
  if ($env:DEERFLOW_NODE_MAX_OLD_SPACE) {
    $nodeOptions = "$nodeOptions --max-old-space-size=$env:DEERFLOW_NODE_MAX_OLD_SPACE"
  }
  $env:NODE_OPTIONS = $nodeOptions
  foreach ($k in $envVars.Keys) { Set-Item -Path ("Env:{0}" -f $k) -Value $envVars[$k] }
  $env:BETTER_AUTH_SECRET = $authSecret
  $env:BETTER_AUTH_URL = "http://localhost:3000"
  $env:BETTER_AUTH_BASE_URL = "http://localhost:3000"
  $env:BETTER_AUTH_DB_DIR = $dbDir
  $env:DEERFLOW_PROXY_SECRET = $proxySecret
  $env:DEERFLOW_GATEWAY_BASE_URL = "http://127.0.0.1:8001"
  if ($allowPublicModels -and -not $env:NEXT_PUBLIC_ALLOW_PUBLIC_MODELS) {
    $env:NEXT_PUBLIC_ALLOW_PUBLIC_MODELS = "true"
  }
  Remove-Item Env:HTTP_PROXY -ErrorAction SilentlyContinue
  Remove-Item Env:http_proxy -ErrorAction SilentlyContinue
  Remove-Item Env:HTTPS_PROXY -ErrorAction SilentlyContinue
  Remove-Item Env:https_proxy -ErrorAction SilentlyContinue
  Remove-Item Env:ALL_PROXY -ErrorAction SilentlyContinue
  Remove-Item Env:all_proxy -ErrorAction SilentlyContinue
  Set-Item -Path "Env:NO_PROXY" -Value "localhost,127.0.0.1,::1,hecc.ai.cnooc"
  Set-Item -Path "Env:no_proxy" -Value "localhost,127.0.0.1,::1,hecc.ai.cnooc"
  cmd /d /s /c "pnpm.cmd run dev 2>&1" |
    Out-File -FilePath $out -Append -Encoding utf8
} -ArgumentList @($frontendCwd, $frontendOut, $betterAuthSecret, $devSecret, $frontendDbDir, $rootEnv, $allowPublicModels)
Pop-Location

$okFrontend = Wait-Listen 3000 120
$okGateway = Wait-Listen 8001 180
$okLanggraph = Wait-Listen 2025 180

Write-Host "ready:"
Write-Host " - frontend:  http://localhost:3000 ($okFrontend)"
Write-Host " - workspace: http://localhost:3000/workspace"
Write-Host " - alt:       http://127.0.0.1:3000"
Write-Host " - gateway:   http://127.0.0.1:8001 ($okGateway)"
Write-Host " - langgraph: http://127.0.0.1:2025 ($okLanggraph)"

try {
  while ($true) {
    Start-Sleep -Seconds 2
    if ($langgraphJob.State -eq "Failed" -or $gatewayJob.State -eq "Failed" -or $frontendJob.State -eq "Failed") {
      break
    }
  }
} finally {
  foreach ($j in @($langgraphJob, $gatewayJob, $frontendJob)) {
    try { Stop-Job -Job $j -Force -ErrorAction SilentlyContinue } catch {}
    try { Remove-Job -Job $j -Force -ErrorAction SilentlyContinue } catch {}
  }
}
