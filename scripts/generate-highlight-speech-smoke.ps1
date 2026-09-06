# Author: Neil Mitchell
# Generates fictional test dialogue only; no raid conversations are read.
param(
    [string]$OutputDirectory = (Join-Path $PSScriptRoot '..\samples\generated\highlight-intelligence')
)

$ErrorActionPreference = 'Stop'
$raidFixtureRoot = [System.IO.Path]::GetFullPath($OutputDirectory)
$raidVoiceDescription = 'Microsoft David Desktop - English (United States)'
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    throw 'Install FFmpeg and make ffmpeg available on PATH before generating this fixture.'
}
$raidVoiceProbe = New-Object -ComObject SAPI.SpVoice
try {
    $raidFixtureVoice = @($raidVoiceProbe.GetVoices() | Where-Object {
        $_.GetDescription() -eq $raidVoiceDescription
    })
    if ($raidFixtureVoice.Count -ne 1) {
        throw "The fixed timing regression requires the Windows voice '$raidVoiceDescription'. Install that voice before running this optional smoke."
    }
}
finally {
    [void][Runtime.InteropServices.Marshal]::ReleaseComObject($raidVoiceProbe)
}
New-Item -ItemType Directory -Force -Path $raidFixtureRoot | Out-Null
$raidVoices = @{
    discord = '<speak><silence msec="1000"/>Okay everyone, this is a completely routine kill. Same boss, same loot as last week.<silence msec="7000"/>Neil, are you sure that shortcut is safe?<silence msec="9000"/>You just charged straight off the edge!<silence msec="5000"/>The shortcut works perfectly. It gets you to the graveyard twice as fast!</speak>'
    microphone = '<speak><silence msec="8500"/>I have discovered a brilliant shortcut. Follow me, I know exactly where I am going.<silence msec="4000"/>Trust me, I have done this a thousand times.<silence msec="5000"/>That was the demonstration of what not to do.</speak>'
}
foreach ($raidRole in $raidVoices.Keys) {
    $raidSpeaker = New-Object -ComObject SAPI.SpVoice
    $raidStream = New-Object -ComObject SAPI.SpFileStream
    try {
        $raidSpeaker.Voice = $raidFixtureVoice[0]
        $raidStream.Open((Join-Path $raidFixtureRoot "$raidRole.wav"), 3)
        $raidSpeaker.AudioOutputStream = $raidStream
        $raidSpeaker.Rate = 0
        [void]$raidSpeaker.Speak($raidVoices[$raidRole], 8)
    }
    finally {
        $raidStream.Close()
        [void][Runtime.InteropServices.Marshal]::ReleaseComObject($raidStream)
        [void][Runtime.InteropServices.Marshal]::ReleaseComObject($raidSpeaker)
    }
}
$raidVideo = Join-Path $raidFixtureRoot 'synthetic-dialogue.mkv'
& ffmpeg -hide_banner -loglevel error -f lavfi -i 'testsrc2=size=640x360:rate=15' -f lavfi -i 'anullsrc=r=48000:cl=stereo' -i (Join-Path $raidFixtureRoot 'discord.wav') -i (Join-Path $raidFixtureRoot 'microphone.wav') -map 0:v -map 1:a -map 2:a -map 3:a -af apad -t 60 -c:v libx264 -preset ultrafast -crf 28 -c:a aac -metadata author='Neil Mitchell' -metadata artist='Neil Mitchell' -metadata creator='Neil Mitchell' -metadata last_modified_by='Neil Mitchell' -metadata title='Synthetic highlight intelligence test; fictional dialogue' -metadata:s:a:0 title='WoW Game' -metadata:s:a:1 title='Discord' -metadata:s:a:2 title='Microphone' -y $raidVideo
if ($LASTEXITCODE -ne 0) { throw 'Synthetic media generation failed' }
$raidTiming = [ordered]@{
    schema_version = 1
    author = 'Neil Mitchell'
    last_modified_by = 'Neil Mitchell'
    fixture = 'Authored fictional speech; not a real raid recording'
    voice = $raidVoiceDescription
    speech_rate = 0
    source_sha256 = (Get-FileHash -LiteralPath $raidVideo -Algorithm SHA256).Hash.ToLowerInvariant()
    expected_first_setup_seconds = 8.21
    expected_final_payoff_seconds = 40.69
    tolerance_seconds = 0.2
}
$raidTiming | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $raidFixtureRoot 'fixture-timing.json') -Encoding utf8
Write-Output $raidVideo
