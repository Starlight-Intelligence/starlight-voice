param(
  [switch]$Dry
)

# Tray icon for the clap listener. Open, start, and stop use the live face.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root "sidecar\.venv\Scripts\python.exe"
$url = "http://127.0.0.1:8765/live"
$startUrl = "http://127.0.0.1:8765/api/listen/start"
$stopUrl = "http://127.0.0.1:8765/api/listen/stop"
$iconPath = Join-Path $root "tauri\icons\icon.ico"

function Test-Dashboard {
  try {
    $response = Invoke-WebRequest -Uri "http://127.0.0.1:8765/healthz" -UseBasicParsing -TimeoutSec 1
    return $response.StatusCode -eq 200
  } catch {
    return $false
  }
}

function Open-Face {
  Start-Process $url
}

function Start-Ara {
  Invoke-RestMethod -Uri $startUrl -Method POST -TimeoutSec 20 | Out-Null
}

function Stop-Ara {
  Invoke-RestMethod -Uri $stopUrl -Method POST -TimeoutSec 20 | Out-Null
}

function Get-RemoteDoor {
  if (-not [string]::IsNullOrWhiteSpace($env:TUNNEL_TOKEN)) {
    return "ready"
  }
  return "blocked TUNNEL_TOKEN"
}

if ($Dry) {
  Write-Output "action open $url"
  Write-Output "action start $startUrl"
  Write-Output "action stop $stopUrl"
  Write-Output "action quit icon-only"
  Write-Output "action remote $(Get-RemoteDoor)"
  exit 0
}

if (-not (Test-Dashboard)) {
  Start-Process -FilePath $python -ArgumentList "dashboard\server.py" -WorkingDirectory $root -WindowStyle Hidden
  Start-Sleep -Seconds 1
}

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$icon = New-Object System.Windows.Forms.NotifyIcon
$icon.Icon = New-Object System.Drawing.Icon $iconPath
$icon.Text = "Starlight Voice"
$icon.Visible = $true

$menu = New-Object System.Windows.Forms.ContextMenuStrip
$open = $menu.Items.Add("Open")
$open.Add_Click({ Open-Face })
$start = $menu.Items.Add("Start Ara")
$start.Add_Click({ Start-Ara })
$stop = $menu.Items.Add("Stop Ara")
$stop.Add_Click({ Stop-Ara })
$remote = $menu.Items.Add("Open remote")
$remote.Add_Click({
  $door = Get-RemoteDoor
  if ($door -eq "ready") {
    $icon.ShowBalloonTip(4000, "Starlight Voice", "TUNNEL_TOKEN is set. The remote door stays closed.", [System.Windows.Forms.ToolTipIcon]::Info)
    return
  }
  $icon.ShowBalloonTip(4000, "Starlight Voice", "Remote door blocked. Missing TUNNEL_TOKEN.", [System.Windows.Forms.ToolTipIcon]::Info)
})
$quit = $menu.Items.Add("Quit tray")
$quit.Add_Click({
  $icon.Visible = $false
  [System.Windows.Forms.Application]::Exit()
})
$icon.ContextMenuStrip = $menu
$icon.Add_DoubleClick({ Open-Face })
Open-Face
[System.Windows.Forms.Application]::Run()
