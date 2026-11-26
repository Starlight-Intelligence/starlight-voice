$ErrorActionPreference = "Stop"
$VoiceRoot = Join-Path $env:USERPROFILE "starlight-voice"
Set-Location $VoiceRoot
$env:PYTHONPATH = "sidecar/src"
$env:STARLIGHT_BRIEF_REPOS = "$env:USERPROFILE\FrankX,$env:USERPROFILE\Starlight-Intelligence-System,$VoiceRoot,$env:USERPROFILE\Arcanea"
$py = Join-Path $VoiceRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }
& $py -m starlight_voice brief --speak