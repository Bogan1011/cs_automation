<#
.SYNOPSIS
  Quick connectivity test from an Apex One agent to its server and Trend update hosts (read-only).
.DESCRIPTION
  Use when agents show offline or patterns do not update. Tests DNS, TCP and HTTPS to the server the
  agent reports to and to any extra hosts given. Changes nothing.
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\apex-agent-connectivity.ps1 -Hosts xxxxxx.manage.trendmicro.com
#>
param([string[]]$Hosts = @())
$ErrorActionPreference = "SilentlyContinue"
$key = "HKLM:\SOFTWARE\WOW6432Node\TrendMicro\PC-cillinNTCorp\CurrentVersion"
if (-not (Test-Path $key)) { $key = "HKLM:\SOFTWARE\TrendMicro\PC-cillinNTCorp\CurrentVersion" }
$cv = Get-ItemProperty $key
$targets = @()
if ($cv.Server) { $targets += [pscustomobject]@{ Host = $cv.Server; Port = [int]$cv.ServerPort } }
foreach ($h in $Hosts) { $targets += [pscustomobject]@{ Host = $h; Port = 443 } }
if (-not $targets) { Write-Host "No server found in the agent settings. Pass -Hosts <server>." -ForegroundColor Yellow; exit 1 }
foreach ($t in $targets) {
  $dns = Resolve-DnsName $t.Host | Where-Object { $_.IPAddress } | Select-Object -First 1
  if (-not $dns) { Write-Host "[FAIL] $($t.Host) does not resolve" -ForegroundColor Red; continue }
  $c = Test-NetConnection -ComputerName $t.Host -Port $t.Port -WarningAction SilentlyContinue
  if ($c.TcpTestSucceeded) { Write-Host "[ OK ] $($t.Host):$($t.Port) reachable ($($dns.IPAddress))" -ForegroundColor Green }
  else { Write-Host "[FAIL] $($t.Host):$($t.Port) not reachable ($($dns.IPAddress))" -ForegroundColor Red }
  try {
    $r = Invoke-WebRequest -Uri ("https://{0}:{1}/" -f $t.Host, $t.Port) -UseBasicParsing -TimeoutSec 10
    Write-Host "       HTTPS answered $($r.StatusCode)"
  } catch {
    $code = $_.Exception.Response.StatusCode.value__
    if ($code) { Write-Host "       HTTPS answered $code (the server is reachable)" } else { Write-Host "       HTTPS failed: $($_.Exception.Message)" -ForegroundColor Yellow }
  }
}
netsh winhttp show proxy | Select-String -Pattern "Proxy|Direct" | ForEach-Object { "WinHTTP " + $_.Line.Trim() }
