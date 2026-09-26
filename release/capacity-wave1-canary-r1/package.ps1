param([string]$OutputDirectory = 'C:/Users/15285/Downloads')
$ErrorActionPreference = 'Stop'
$packageName = 'deerflow-capacity-wave1-canary-20260924-r1'
$packageStage = Join-Path $OutputDirectory $packageName
$packageArchive = Join-Path $OutputDirectory ($packageName + '.tar.gz')
if ((Test-Path -LiteralPath $packageStage) -or (Test-Path -LiteralPath $packageArchive)) {
    throw 'Delivered output already exists; never overwrite'
}
$null = New-Item -ItemType Directory -Path $packageStage
$utf8 = New-Object System.Text.UTF8Encoding($false)
$files = @('canary-wave1.py', 'http-probe.cjs', 'prepare-wave1.py', 'README.md')
foreach ($name in $files) {
    $source = Join-Path $PSScriptRoot $name
    if ($name -eq 'prepare-wave1.py') { $source = Join-Path $PSScriptRoot '../capacity-wave1-r1/prepare-wave1.py' }
    $text = [IO.File]::ReadAllText($source).Replace("`r`n", "`n")
    [IO.File]::WriteAllText((Join-Path $packageStage $name), $text, $utf8)
}
$manifest = foreach ($name in $files) {
    $hash = (Get-FileHash -LiteralPath (Join-Path $packageStage $name) -Algorithm SHA256).Hash.ToLowerInvariant()
    "$hash  $name"
}
[IO.File]::WriteAllText((Join-Path $packageStage 'PACKAGE.SHA256'), (($manifest -join "`n") + "`n"), $utf8)
& tar -czf $packageArchive -C $OutputDirectory $packageName
if ($LASTEXITCODE -ne 0) { throw 'tar failed' }
$archiveHash = (Get-FileHash -LiteralPath $packageArchive -Algorithm SHA256).Hash.ToLowerInvariant()
[IO.File]::WriteAllText(($packageArchive + '.sha256'), "$archiveHash  $packageName.tar.gz`n", $utf8)
Get-Item -LiteralPath $packageArchive, ($packageArchive + '.sha256') | Select-Object FullName,Length
