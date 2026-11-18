<#
.SYNOPSIS
  Creates the Deep Security Agent diagnostic package for Trend support (Windows).
.DESCRIPTION
  Runs the agent's own "dsa_control -d" command, which writes a zip of agent logs and settings.
  It does not change any setting. Attach the zip to the Trend case.
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\ds-collect-diagnostics-windows.ps1
#>
$dir = "C:\Program Files\Trend Micro\Deep Security Agent"
$ctl = Join-Path $dir "dsa_control.cmd"
if (-not (Test-Path $ctl)) { Write-Host "dsa_control.cmd not found in $dir" -ForegroundColor Red; exit 1 }
Write-Host "Creating the diagnostic package. This can take a few minutes..." -ForegroundColor Cyan
& $ctl -d
$zip = Get-ChildItem (Join-Path $dir "diag") -Filter *.zip -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($zip) { Write-Host "Package: $($zip.FullName)" -ForegroundColor Green } else { Write-Host "Look for the newest zip under $dir\diag" -ForegroundColor Yellow }
