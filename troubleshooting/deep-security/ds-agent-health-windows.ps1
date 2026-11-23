<#
.SYNOPSIS
  Deep Security / Server & Workload Protection agent health check for Windows (read-only).
.DESCRIPTION
  Checks the agent services, agent version and status, the connection to the manager,
  recent errors in the event log and free disk space. Changes nothing on the machine.
  The report is saved to %TEMP%\DSA-Health-<computer>-<time>.txt to attach to a case.
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\ds-agent-health-windows.ps1
  powershell -ExecutionPolicy Bypass -File .\ds-agent-health-windows.ps1 -Manager workload.in-1.cloudone.trendmicro.com
#>
param(
  [string]$Manager = "",   # manager / relay host to test. Default: read from the agent.
  [int[]]$Ports = @(443, 4120, 4122)
)
$ErrorActionPreference = "SilentlyContinue"
$report = Join-Path $env:TEMP ("DSA-Health-{0}-{1:yyyyMMdd-HHmm}.txt" -f $env:COMPUTERNAME, (Get-Date))
Start-Transcript -Path $report | Out-Null
function Section($t) { Write-Host ""; Write-Host ("=== " + $t + " ===") -ForegroundColor Cyan }
function Ok($t)   { Write-Host ("[ OK ] " + $t) -ForegroundColor Green }
function Bad($t)  { Write-Host ("[FAIL] " + $t) -ForegroundColor Red }
function Warn($t) { Write-Host ("[WARN] " + $t) -ForegroundColor Yellow }

Section "Machine"
$os = Get-CimInstance Win32_OperatingSystem
"{0}  {1} build {2}  last boot {3}" -f $env:COMPUTERNAME, $os.Caption, $os.BuildNumber, $os.LastBootUpTime

Section "Agent services"
$expected = "ds_agent", "ds_monitor", "ds_notifier", "Amsp"
foreach ($n in $expected) {
  $s = Get-Service -Name $n
  if (-not $s) { Warn "$n not installed" }
  elseif ($s.Status -eq "Running") { Ok "$n ($($s.DisplayName)) running, start type $($s.StartType)" }
  else { Bad "$n ($($s.DisplayName)) is $($s.Status), start type $($s.StartType)" }
}
Get-Service | Where-Object { $_.DisplayName -like "Trend Micro*" -and $expected -notcontains $_.Name } |
  ForEach-Object { "      also: {0} ({1}) {2}" -f $_.Name, $_.DisplayName, $_.Status }

Section "Agent version and status"
$dir = "C:\Program Files\Trend Micro\Deep Security Agent"
if (-not (Test-Path $dir)) { Bad "Agent folder not found: $dir" }
else {
  $exe = Join-Path $dir "dsa.exe"
  if (Test-Path $exe) { "Agent version: " + (Get-Item $exe).VersionInfo.ProductVersion }
  $q = Join-Path $dir "dsa_query.cmd"
  if (Test-Path $q) {
    $status = & $q -c GetAgentStatus 2>&1
    $status | Select-String -Pattern "AgentStatus|dsmUrl|dsm|Activat|Heartbeat|Feature|Mode" | ForEach-Object { "  " + $_.Line.Trim() }
    if (-not $Manager) {
      $m = $status | Select-String -Pattern "https?://([^:/\s]+)" | Select-Object -First 1
      if ($m) { $Manager = $m.Matches[0].Groups[1].Value }
    }
  } else { Warn "dsa_query.cmd not found" }
}

Section "Connection to manager"
if (-not $Manager) { Warn "Manager host unknown. Run again with -Manager <host>." }
else {
  foreach ($p in $Ports) {
    $t = Test-NetConnection -ComputerName $Manager -Port $p -WarningAction SilentlyContinue
    if ($t.TcpTestSucceeded) { Ok "$Manager`:$p reachable ($($t.RemoteAddress))" } else { Bad "$Manager`:$p NOT reachable" }
  }
}
"WinHTTP proxy:"; netsh winhttp show proxy | Select-String -Pattern "Proxy|Direct" | ForEach-Object { "  " + $_.Line.Trim() }

Section "Agent errors in the last 3 days (Application log)"
$since = (Get-Date).AddDays(-3)
$ev = Get-WinEvent -FilterHashtable @{ LogName = "Application"; Level = 1, 2; StartTime = $since } |
  Where-Object { $_.ProviderName -match "Deep Security|ds_agent|Trend|AMSP" } | Select-Object -First 15
if ($ev) { $ev | ForEach-Object { "{0:yyyy-MM-dd HH:mm}  {1}  {2}" -f $_.TimeCreated, $_.ProviderName, ($_.Message -split "`n")[0] } }
else { Ok "No agent errors" }

Section "Disk and time"
Get-PSDrive -PSProvider FileSystem | Where-Object { $_.Used -ne $null } | ForEach-Object {
  $freeGB = [math]::Round($_.Free / 1GB, 1)
  if ($freeGB -lt 2) { Bad "$($_.Name): only $freeGB GB free" } else { Ok "$($_.Name): $freeGB GB free" }
}
"Time source: " + (w32tm /query /source 2>&1)
"Local time: {0}  (a clock that is out by several minutes breaks the agent's TLS connection)" -f (Get-Date)

Section "Pending reboot"
$pending = (Test-Path "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending") -or
           (Test-Path "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired")
if ($pending) { Warn "A reboot is pending. Agent upgrades and driver loads can wait for it." } else { Ok "No reboot pending" }

Section "Next steps"
"- Service stopped: start it (services.msc). If it stops again, collect a diagnostic package with ds-collect-diagnostics-windows.ps1."
"- Manager not reachable: check firewall and proxy for the ports above."
"- Agent offline in the console but connected here: send a heartbeat from the agent: & '$dir\dsa_control.cmd' -m"
Stop-Transcript | Out-Null
Write-Host "`nReport saved to $report" -ForegroundColor Cyan
