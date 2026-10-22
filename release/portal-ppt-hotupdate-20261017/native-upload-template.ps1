param([switch]$PlanOnly)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$manifestPath = Join-Path $PSScriptRoot 'UPLOAD-MANIFEST.json'
$expectedManifestHash = '__UPLOAD_MANIFEST_SHA256__'
$actualManifestHash = (Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actualManifestHash -ne $expectedManifestHash) { throw 'Upload manifest hash mismatch.' }
$manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($manifest.destination -ne 'cnooc@192.168.111.2' -or $manifest.directory -ne '/home/cnooc/portal-ppt-native-preload-20261017-r1') {
    throw 'Unexpected upload destination.'
}
if ($manifest.archives.Count -ne 5) { throw 'Exactly five local archives are required.' }

$sources = [System.Collections.Generic.List[string]]::new()
$names = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::Ordinal)
foreach ($row in @($manifest.tools) + @($manifest.archives)) {
    $path = if ($row.kind -eq 'tool') { Join-Path $PSScriptRoot $row.name } else { [string]$row.path }
    $file = Get-Item -LiteralPath $path
    if ($file.PSIsContainer -or ($file.Attributes -band [System.IO.FileAttributes]::ReparsePoint)) { throw "Not a regular input: $path" }
    if ($file.Length -ne $row.bytes -or $file.Name -ne $row.name -or -not $names.Add($file.Name)) { throw "Input size or name mismatch: $path" }
    if ((-not $PlanOnly) -or $row.kind -eq 'tool') {
        Write-Host ('Verify ' + $row.name)
        if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $row.sha256) { throw "Input hash mismatch: $path" }
    }
    $sources.Add($file.FullName)
}
if ($PlanOnly) {
    Write-Host ('PLAN ONLY: ' + $manifest.archives.Count + ' existing archives, ' + $manifest.archive_bytes + ' bytes.')
    Write-Host 'No server connection or upload. Large archive hashes are checked by the actual upload mode.'
    exit 0
}

Get-Command ssh -ErrorAction Stop | Out-Null
Get-Command scp -ErrorAction Stop | Out-Null
# Send the fixed bootstrap on stdin: no nested PowerShell/shell quoting of paths.
$bootstrap = @'
import json, os, pathlib, socket
p = pathlib.Path("/home/cnooc/portal-ppt-native-preload-20261017-r1")
if socket.gethostname().split(".")[0] != "datax-01":
    raise SystemExit("target_hostname_mismatch")
if p.exists() or p.is_symlink():
    raise SystemExit("upload_directory_exists_preserve_for_review")
s = os.statvfs(str(p.parent))
free = s.f_bavail * s.f_frsize
required = 3818267648 + 2 * 1024**3
if free < required:
    raise SystemExit("insufficient_upload_filesystem_space")
os.umask(0o077)
p.mkdir(mode=0o700)
print(json.dumps({"upload_directory":str(p),"available_bytes":free,"required_bytes":required}))
'@
$bootstrap | & ssh -T -o ConnectTimeout=15 -o StrictHostKeyChecking=ask $manifest.destination 'python3 -I -B -'
if ($LASTEXITCODE -ne 0) { throw 'Remote directory preparation failed. No automatic retry; preserve any existing directory.' }

$scpArgs = @('-o', 'ConnectTimeout=15', '-o', 'StrictHostKeyChecking=yes') + $sources.ToArray() + @($manifest.destination + ':' + $manifest.directory + '/')
& scp @scpArgs
if ($LASTEXITCODE -ne 0) { throw 'Upload failed. Partial files are preserved; do not rerun before review.' }
Write-Host 'Upload complete. The server preload tool will independently verify every received archive.'
Write-Host 'The Host archive remains on datax-01 and was not uploaded from Windows.'
Write-Host $manifest.server_command
