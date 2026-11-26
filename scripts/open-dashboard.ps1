$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
  throw "venv not found at $python — run scripts/install-jarvis-stack.ps1 first"
}

$port = if ($env:STARLIGHT_DASHBOARD_PORT) { $env:STARLIGHT_DASHBOARD_PORT } else { "8765" }
$cockpitUrl = "http://127.0.0.1:$port/dashboard/cockpit.html"
$healthUrl = "http://127.0.0.1:$port/healthz"
$server = $null

function Test-DashboardUp {
  try {
    $response = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 2
    return $response.StatusCode -eq 200
  } catch {
    return $false
  }
}

if (-not (Test-DashboardUp)) {
  $env:PYTHONPATH = Join-Path $root "sidecar\src"
  $server = Start-Process `
    -FilePath $python `
    -ArgumentList "dashboard/server.py" `
    -WorkingDirectory $root `
    -PassThru `
    -WindowStyle Hidden

  $deadline = (Get-Date).AddSeconds(10)
  while ((Get-Date) -lt $deadline) {
    if (Test-DashboardUp) { break }
    Start-Sleep -Milliseconds 300
  }

  if (-not (Test-DashboardUp)) {
    if ($server -and -not $server.HasExited) {
      Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue
    }
    throw "Dashboard failed to start on $healthUrl — check .venv and run: .\.venv\Scripts\python.exe dashboard/server.py"
  }
}

Start-Process $cockpitUrl

Write-Host "Starlight Voice cockpit opened: $cockpitUrl"
if ($server) {
  Write-Host "Server PID: $($server.Id)"
} else {
  Write-Host "Server already running on 127.0.0.1:$port"
}
Write-Host "Ratings file: $root\dashboard\ratings.jsonl"