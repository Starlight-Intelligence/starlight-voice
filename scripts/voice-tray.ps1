# Tray icon for the clap listener. Opens the live page. Does not start a second listen.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root "sidecar\.venv\Scripts\python.exe"
$url = "http://127.0.0.1:8765/live"
$iconPath = Join-Path $root "tauri\icons\icon.ico"

function Test-Dashboard {
  try {
    $response = Invoke-WebRequest -Uri "http://127.0.0.1:8765/healthz" -UseBasicParsing -TimeoutSec 1
    return $response.StatusCode -eq 200
  } catch {
    return $false
  }
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
$open = $menu.Items.Add("Open voice")
$open.Add_Click({ Start-Process $url })
$quit = $menu.Items.Add("Quit tray")
$quit.Add_Click({
  $icon.Visible = $false
  [System.Windows.Forms.Application]::Exit()
})
$icon.ContextMenuStrip = $menu
$icon.Add_DoubleClick({ Start-Process $url })
Start-Process $url
[System.Windows.Forms.Application]::Run()
