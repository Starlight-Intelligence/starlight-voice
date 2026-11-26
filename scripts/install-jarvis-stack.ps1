# Starlight + OpenJarvis + CloudCLI machine bootstrap
# Run: pwsh -File scripts/install-jarvis-stack.ps1
$ErrorActionPreference = "Stop"

$FrankHome = $env:USERPROFILE
$VoiceRoot = Join-Path $FrankHome "starlight-voice"
$OpenJarvisRoot = Join-Path $FrankHome "OpenJarvis"
$SisRoot = Join-Path $FrankHome "Starlight-Intelligence-System"

Write-Host "=== Frank Jarvis Stack Installer ===" -ForegroundColor Cyan

# 1. starlight-voice Python venv + extras
Set-Location $VoiceRoot
if (-not (Test-Path ".venv")) {
    uv venv .venv
}
$py = Join-Path $VoiceRoot ".venv\Scripts\python.exe"
uv pip install -e "sidecar[voice,providers,browser,dev]" | Out-Null
Write-Host "[ok] starlight-voice sidecar deps" -ForegroundColor Green

# 2. OpenJarvis (clone if missing, sync server extra, init if no config)
if (-not (Test-Path $OpenJarvisRoot)) {
    git clone --depth 1 https://github.com/open-jarvis/OpenJarvis.git $OpenJarvisRoot
}
Set-Location $OpenJarvisRoot
uv sync --extra server | Out-Null
$config = Join-Path $env:USERPROFILE ".openjarvis\config.toml"
if (-not (Test-Path $config)) {
    uv run jarvis init --preset chat-simple
}
Write-Host "[ok] OpenJarvis ($OpenJarvisRoot)" -ForegroundColor Green

# 3. CloudCLI (global npm)
if (-not (Get-Command cloudcli -ErrorAction SilentlyContinue)) {
    npm install -g @cloudcli-ai/cloudcli
}
Write-Host "[ok] CloudCLI $(cloudcli --version)" -ForegroundColor Green

# 4. Build Tauri tray (release)
Set-Location $VoiceRoot
cargo build --release -p starlight-voice-tauri
Write-Host "[ok] Tauri shell built" -ForegroundColor Green

# 5. Machine profile shims
$JarvisBin = Join-Path $FrankHome ".local\bin"
New-Item -ItemType Directory -Force -Path $JarvisBin | Out-Null

@'
@echo off
cd /d "%USERPROFILE%\OpenJarvis"
uv run jarvis %*
'@ | Set-Content (Join-Path $JarvisBin "jarvis.cmd") -Encoding ASCII

@'
@echo off
cd /d "%USERPROFILE%\starlight-voice"
set PYTHONPATH=sidecar\src
"%USERPROFILE%\starlight-voice\.venv\Scripts\python.exe" -m starlight_voice %*
'@ | Set-Content (Join-Path $JarvisBin "starlight-voice.cmd") -Encoding ASCII

# 6. Env defaults for proactive brief + SIS gateway
$envFile = Join-Path $VoiceRoot ".env.local"
if (-not (Test-Path $envFile)) {
    @"
STARLIGHT_SIS_ROOT=$SisRoot
STARLIGHT_BRIEF_REPOS=$FrankHome\FrankX,$SisRoot,$VoiceRoot,$FrankHome\Arcanea,$FrankHome\agentic-creator-os
"@ | Set-Content $envFile -Encoding UTF8
}

# 7. Scheduled task: morning brief 04:30
$taskName = "StarlightMorningBrief"
$briefScript = Join-Path $VoiceRoot "scripts\morning-brief.ps1"
$action = New-ScheduledTaskAction -Execute "pwsh.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$briefScript`""
$trigger = New-ScheduledTaskTrigger -Daily -At "04:30"
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Write-Host "[ok] Scheduled task: $taskName @ 04:30" -ForegroundColor Green

Write-Host ""
Write-Host "Next: pwsh -File scripts/start-jarvis.ps1" -ForegroundColor Yellow
Write-Host "PTT: Ctrl+Shift+Space | Cockpit: http://127.0.0.1:8765/dashboard/cockpit.html" -ForegroundColor Yellow
Write-Host "Mobile: cloudcli (http://localhost:3001)" -ForegroundColor Yellow
Write-Host "OpenJarvis: jarvis (from $JarvisBin if on PATH)" -ForegroundColor Yellow