# Start the full Frank Jarvis stack (tray, cockpit, CloudCLI, OpenJarvis API, SIS gateway)
$ErrorActionPreference = "Continue"

$FrankHome = $env:USERPROFILE
$VoiceRoot = Join-Path $FrankHome "starlight-voice"
$OpenJarvisRoot = Join-Path $FrankHome "OpenJarvis"
$SisRoot = Join-Path $FrankHome "Starlight-Intelligence-System"

Set-Location $VoiceRoot
$env:PYTHONPATH = "sidecar/src"
$env:STARLIGHT_SIS_ROOT = $SisRoot
$env:STARLIGHT_BRIEF_REPOS = "$FrankHome\FrankX,$SisRoot,$VoiceRoot,$FrankHome\Arcanea,$FrankHome\agentic-creator-os"

function Start-Hidden([string]$Exe, [string[]]$Args) {
    Start-Process -FilePath $Exe -ArgumentList $Args -WindowStyle Hidden -ErrorAction SilentlyContinue
}

Write-Host "=== Starting Frank Jarvis Stack ===" -ForegroundColor Cyan

# SIS memory gateway: check if shared loopback bridge at :8767 is already healthy
$SharedBridgeUrl = "http://127.0.0.1:8767/health"
$BridgeHealthy = $false
try {
    $resp = Invoke-RestMethod -Uri $SharedBridgeUrl -Method Get -TimeoutSec 2 -ErrorAction Stop
    $BridgeHealthy = $true
    Write-Host "[reuse] Shared Starlight MCP Bridge active at 127.0.0.1:8767 (skipping private gateway spawn)" -ForegroundColor Green
} catch {
    $BridgeHealthy = $false
}

if (-not $BridgeHealthy) {
    $gatewayScript = Join-Path $SisRoot "package.json"
    if (Test-Path $gatewayScript) {
        Push-Location $SisRoot
        if (Get-Command pnpm -ErrorAction SilentlyContinue) {
            Start-Hidden "pwsh" @("-NoProfile", "-Command", "cd '$SisRoot'; pnpm run gateway:start")
            Write-Host "[start] SIS memory gateway (fallback standalone: pnpm run gateway:start)" -ForegroundColor Yellow
        }
        Pop-Location
    }
}

# Starlight cockpit dashboard
Start-Hidden "pwsh" @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $VoiceRoot "scripts\open-dashboard.ps1"))
Write-Host "[start] Starlight cockpit -> http://127.0.0.1:8765/dashboard/cockpit.html" -ForegroundColor Green

# CloudCLI mobile/web session UI
if (Get-Command cloudcli -ErrorAction SilentlyContinue) {
    Start-Hidden "cloudcli" @()
    Write-Host "[start] CloudCLI -> http://localhost:3001" -ForegroundColor Green
}

# OpenJarvis API server (OpenAI-compatible, local)
if (Test-Path $OpenJarvisRoot) {
    Push-Location $OpenJarvisRoot
    Start-Hidden "uv" @("run", "jarvis", "serve", "--host", "127.0.0.1", "--port", "8766")
    Write-Host "[start] OpenJarvis API -> http://127.0.0.1:8766" -ForegroundColor Green
    Pop-Location
}

# Tauri tray (PTT + autostart)
$tray = Join-Path $VoiceRoot "target\release\starlight-voice-tauri.exe"
if (Test-Path $tray) {
    Start-Process $tray -WindowStyle Hidden
    Write-Host "[start] Starlight Voice tray (PTT: Ctrl+Shift+Space)" -ForegroundColor Green
} else {
    Write-Host "[warn] Tray not built. Run: cargo build --release -p starlight-voice-tauri" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Doctor:" -ForegroundColor Cyan
$py = Join-Path $VoiceRoot ".venv\Scripts\python.exe"
if (Test-Path $py) { & $py -m starlight_voice doctor } else { python -m starlight_voice doctor }
if (Get-Command arco -ErrorAction SilentlyContinue) { arco status }