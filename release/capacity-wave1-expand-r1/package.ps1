param([string]$OutputDirectory = 'C:/Users/15285/Downloads')
$ErrorActionPreference = 'Stop'
$packageName = 'deerflow-capacity-wave1-expand-20260924-r1'
$stage = Join-Path $OutputDirectory $packageName
$archive = Join-Path $OutputDirectory ($packageName + '.tar.gz')
if ((Test-Path -LiteralPath $stage) -or (Test-Path -LiteralPath $archive)) {
    throw 'Delivered output already exists; do not overwrite'
}
$null = New-Item -ItemType Directory -Path $stage
$utf8 = New-Object System.Text.UTF8Encoding($false)
$files = @('expand-wave1.py', 'prepare-wave1.py', 'README.md')
foreach ($name in $files) {
    $source = Join-Path $PSScriptRoot $name
    if ($name -eq 'prepare-wave1.py') { $source = Join-Path $PSScriptRoot '../capacity-wave1-r1/prepare-wave1.py' }
    $text = [IO.File]::ReadAllText($source).Replace("`r`n", "`n")
    [IO.File]::WriteAllText((Join-Path $stage $name), $text, $utf8)
}
$manifest = foreach ($name in $files) {
    $hash = (Get-FileHash -LiteralPath (Join-Path $stage $name) -Algorithm SHA256).Hash.ToLowerInvariant()
    "$hash  $name"
}
[IO.File]::WriteAllText((Join-Path $stage 'PACKAGE.SHA256'), (($manifest -join "`n") + "`n"), $utf8)
& tar -czf $archive -C $OutputDirectory $packageName
if ($LASTEXITCODE -ne 0) { throw 'tar failed' }
$archiveHash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
[IO.File]::WriteAllText(($archive + '.sha256'), "$archiveHash  $packageName.tar.gz`n", $utf8)
Get-Item -LiteralPath $archive, ($archive + '.sha256') | Select-Object FullName,Length
