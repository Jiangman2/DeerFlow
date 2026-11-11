<#
.SYNOPSIS
    Switch the Feishu channel API domain between public Feishu and the CNOOC deployment.

.DESCRIPTION
    Rewrites the single ``FEISHU_DOMAIN=`` line in the repository ``.env``. That
    value is referenced from ``config.yaml`` as
    ``channels.feishu.domain: $FEISHU_DOMAIN`` and resolved by
    ``AppConfig.resolve_env_variables()`` when the Gateway loads its config.

    The Gateway reads ``.env`` once at process start, so restart the Gateway
    after switching for the change to take effect.

    Note: this is NOT ``DEERFLOW_FEISHU_DOMAIN``. That environment variable is a
    separate guard inside ``app/channels/runtime_config_store.py`` that only
    affects runtime-config-store overrides and fails closed to
    ``https://open.w.cnooc.com.cn`` when set. Leave it unset.

.PARAMETER Domain
    feishu -> https://open.feishu.cn        (public China Feishu, default)
    cnooc  -> https://open.w.cnooc.com.cn   (CNOOC private deployment)

.EXAMPLE
    ./scripts/switch-feishu-domain.ps1 cnooc

.EXAMPLE
    ./scripts/switch-feishu-domain.ps1 feishu
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet("feishu", "cnooc")]
    [string]$Domain
)

$ErrorActionPreference = "Stop"

$domains = @{
    feishu = "https://open.feishu.cn"
    cnooc  = "https://open.w.cnooc.com.cn"
}
$target = $domains[$Domain]

$repoRoot = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $repoRoot ".env"
if (-not (Test-Path -LiteralPath $envPath -PathType Leaf)) {
    throw "Missing $envPath - create it from .env.example / config.example.yaml first."
}

$lines = [System.Collections.Generic.List[string]](Get-Content -LiteralPath $envPath)
$pattern = '^\s*#?\s*FEISHU_DOMAIN\s*=(.*)$'
$previous = $null
$replaced = $false

for ($i = 0; $i -lt $lines.Count; $i++) {
    if ($lines[$i] -match $pattern) {
        if (-not $replaced) {
            $previous = $Matches[1].Trim()
            $lines[$i] = "FEISHU_DOMAIN=$target"
            $replaced = $true
        }
    }
}

if (-not $replaced) {
    if ($lines.Count -gt 0 -and $lines[$lines.Count - 1] -ne "") {
        $lines.Add("")
    }
    $lines.Add("# Feishu API domain, switched by scripts/switch-feishu-domain.ps1")
    $lines.Add("FEISHU_DOMAIN=$target")
}

# Write UTF-8 without a BOM: Windows PowerShell 5.1's `-Encoding utf8` adds one,
# and a BOM would corrupt the first key parsed out of .env.
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllLines($envPath, $lines, $utf8NoBom)

Write-Host "Feishu domain switched to: $target" -ForegroundColor Green
if ($previous) {
    Write-Host "  previous value: $previous"
}
Write-Host ""
Write-Host "Restart the Gateway to apply it (the .env is read once at startup)." -ForegroundColor Yellow
Write-Host "Verify in the Gateway log for: [Feishu] using domain: $target"