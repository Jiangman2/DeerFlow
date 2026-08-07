param(
    [string]$InputDir = "docs\podcast\scripts",
    [string]$OutputDir = "docs\podcast\audio",
    [int]$Episode = 0
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$inputPath = Join-Path $root $InputDir
$outputPath = Join-Path $root $OutputDir
New-Item -ItemType Directory -Force -Path $outputPath | Out-Null

Add-Type -AssemblyName System.Speech
$voice = New-Object System.Speech.Synthesis.SpeechSynthesizer
$preferred = $voice.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Culture.Name -eq "zh-CN" } | Select-Object -First 1
if (-not $preferred) {
    throw "No zh-CN SpeechSynthesizer voice is installed."
}
$voice.SelectVoice($preferred.VoiceInfo.Name)
$voice.Rate = 0
$voice.Volume = 100

$files = Get-ChildItem -LiteralPath $inputPath -Filter "episode-*.txt" | Sort-Object Name
if ($Episode -gt 0) {
    $needle = "episode-{0:00}.txt" -f $Episode
    $files = $files | Where-Object { $_.Name -eq $needle }
}

foreach ($file in $files) {
    $base = [IO.Path]::GetFileNameWithoutExtension($file.Name)
    $wav = Join-Path $outputPath ($base + ".wav")
    $mp3 = Join-Path $outputPath ($base + ".mp3")
    Write-Host "Synthesizing $($file.Name) with $($preferred.VoiceInfo.Name)"
    $voice.SetOutputToWaveFile($wav)
    $paragraphs = (Get-Content -LiteralPath $file.FullName -Encoding utf8) -join "`n"
    foreach ($paragraph in ($paragraphs -split "`n\s*`n")) {
        if ($paragraph.Trim().Length -gt 0) {
            $voice.Speak($paragraph.Trim())
        }
    }
    $voice.SetOutputToNull()
    & ffmpeg -y -loglevel error -i $wav -codec:a libmp3lame -b:a 96k $mp3
    Remove-Item -LiteralPath $wav -Force
    Write-Host "Created $mp3"
}

$voice.Dispose()
