# Starlight Jarvis — one-command launcher.
#   .\scripts\jarvis.ps1            # talk: opens mic + speakers, full voice loop
#   .\scripts\jarvis.ps1 clap       # clap to summon: double-clap launches the loop
#   .\scripts\jarvis.ps1 smoke      # headless end-to-end proof (no mic) — TTS/STT/LLM
#   .\scripts\jarvis.ps1 doctor     # machine + engine readiness report
#   .\scripts\jarvis.ps1 voice --selftest   # assemble the graph headlessly
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$env:PYTHONPATH = "sidecar/src"
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { Write-Error "sidecar venv missing at $py — run: python -m venv .venv"; exit 1 }

$mode = if ($args.Count -gt 0) { $args[0] } else { "voice" }
$rest = if ($args.Count -gt 1) { $args[1..($args.Count-1)] } else { @() }

switch ($mode) {
    "clap"   { & $py -m starlight_voice clap --launch-voice }
    "voice"  { if ($rest) { & $py -m starlight_voice voice @rest } else { & $py -m starlight_voice voice --run } }
    "smoke"  { & $py scripts\smoke_voice_e2e.py }
    "doctor" { & $py -m starlight_voice doctor }
    default  { & $py -m starlight_voice @args }
}
