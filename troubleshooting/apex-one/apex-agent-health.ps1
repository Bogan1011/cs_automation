<#
.SYNOPSIS
  Apex One / Standard Endpoint Protection agent health check for Windows (read-only).
.DESCRIPTION
  Checks the agent services, the server the agent reports to, pattern and engine versions from the
  registry, the connection to the server, the agent listening port, recent errors, disk and time.
  Changes nothing on the machine. Report: %TEMP%\Apex-Health-<computer>-<time>.txt
.PARAMETER Server
  Apex One server to test, for example xxxxxx.manage.trendmicro.com. Default: read from the agent.
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\apex-agent-health.ps1
#>
param([string]$Server = "", [int]$Port = 0)
$ErrorActionPreference = "SilentlyContinue"
$report = Join-Path $env:TEMP ("Apex-Health-{0}-{1:yyyyMMdd-HHmm}.txt" -f $env:COMPUTERNAME, (Get-Date))
Start-Transcript -Path $report | Out-Null
function Section($t) { Write-Host ""; Write-Host ("=== " + $t + " ===") -ForegroundColor Cyan }
function Ok($t)   { Write-Host ("[ OK ] " + $t) -ForegroundColor Green }
function Bad($t)  { Write-Host ("[FAIL] " + $t) -ForegroundColor Red }
function Warn($t) { Write-Host ("[WARN] " + $t) -ForegroundColor Yellow }

Section "Machine"
$os = Get-CimInstance Win32_OperatingSystem
"{0}  {1} build {2}  last boot {3}" -f $env:COMPUTERNAME, $os.Caption, $os.BuildNumber, $os.LastBootUpTime

Section "Agent services"
# Core Apex One agent services. Some are only present when the feature is enabled.
$core = [ordered]@{
  "ntrtscan"  = "Real-time Scan (required)"
  "tmlisten"  = "Listener, receives commands from the server (required)"
  "TmCCSF"    = "Common Client Solution Framework (required)"
  "TMBMServer"= "Unauthorized Change Prevention / behaviour monitoring"
  "TmPfw"     = "Personal firewall (only if enabled)"
  "TmWSCSvc"  = "Security Center integration"
}
foreach ($n in $core.Keys) {
  $s = Get-Service -Name $n
  if (-not $s) { if ($core[$n] -match "required") { Bad "$n missing: $($core[$n])" } else { "  $n not installed ($($core[$n]))" } }
  elseif ($s.Status -eq "Running") { Ok "$n running: $($core[$n])" }
  else { Bad "$n is $($s.Status) (start type $($s.StartType)): $($core[$n])" }
}
Get-Service | Where-Object { $_.DisplayName -match "Apex One|Trend Micro|OfficeScan" -and -not $core.Contains($_.Name) } |
  ForEach-Object { "      also: {0} ({1}) {2}" -f $_.Name, $_.DisplayName, $_.Status }

Section "Agent settings and patterns"
$key = "HKLM:\SOFTWARE\WOW6432Node\TrendMicro\PC-cillinNTCorp\CurrentVersion"
if (-not (Test-Path $key)) { $key = "HKLM:\SOFTWARE\TrendMicro\PC-cillinNTCorp\CurrentVersion" }
if (-not (Test-Path $key)) { Bad "Agent registry key not found. The agent is not installed." }
else {
  $cv = Get-ItemProperty $key
  if (-not $Server) { $Server = $cv.Server }
  if (-not $Port) { $Port = [int]$cv.ServerPort }
  "  Server: {0}  port {1}  agent listen port {2}" -f $cv.Server, $cv.ServerPort, $cv.LocalServerPort
  "  Program folder: " + $cv.'Application Path'
  $exe = Join-Path $cv.'Application Path' "PccNTMon.exe"
  if (Test-Path $exe) { "  Agent build: " + (Get-Item $exe).VersionInfo.ProductVersion }
  $misc = Get-ItemProperty (Join-Path $key "Misc.")
  if ($misc) {
    $misc.PSObject.Properties | Where-Object { $_.Name -match "Pattern|Engine|Ver$|UpdateTime|LastUpdate" -and $_.Name -notmatch "^PS" } |
      Sort-Object Name | ForEach-Object { "  {0} = {1}" -f $_.Name, $_.Value }
  }
}

Section "Connection to the server"
if (-not $Server) { Warn "Server unknown. Run again with -Server <host>." }
else {
  $ports = @($Port, 443) | Where-Object { $_ } | Select-Object -Unique
  foreach ($p in $ports) {
    $t = Test-NetConnection -ComputerName $Server -Port $p -WarningAction SilentlyContinue
    if ($t.TcpTestSucceeded) { Ok "$Server`:$p reachable ($($t.RemoteAddress))" } else { Bad "$Server`:$p NOT reachable" }
  }
}
if ($cv.LocalServerPort) {
  $l = Get-NetTCPConnection -LocalPort $cv.LocalServerPort -State Listen
  if ($l) { Ok "Agent is listening on port $($cv.LocalServerPort) (server can reach it if the firewall allows)" } else { Warn "Nothing listening on agent port $($cv.LocalServerPort). Check the tmlisten service." }
}
"WinHTTP proxy:"; netsh winhttp show proxy | Select-String -Pattern "Proxy|Direct" | ForEach-Object { "  " + $_.Line.Trim() }

Section "Apex One errors in the last 3 days"
$ev = Get-WinEvent -FilterHashtable @{ LogName = "Application", "System"; Level = 1, 2; StartTime = (Get-Date).AddDays(-3) } |
  Where-Object { $_.ProviderName -match "Trend|Apex|OfficeScan|ntrtscan|tmlisten|TMBM" } | Select-Object -First 15
if ($ev) { $ev | ForEach-Object { "{0:yyyy-MM-dd HH:mm}  {1}  {2}" -f $_.TimeCreated, $_.ProviderName, ($_.Message -split "`n")[0] } } else { Ok "No Apex One errors" }

Section "Disk, time and reboot"
Get-PSDrive -PSProvider FileSystem | Where-Object { $_.Used -ne $null } | ForEach-Object {
  $g = [math]::Round($_.Free / 1GB, 1); if ($g -lt 2) { Bad "$($_.Name): only $g GB free (pattern updates need space)" } else { Ok "$($_.Name): $g GB free" } }
"Local time {0}, time source {1}" -f (Get-Date), (w32tm /query /source 2>&1)
$pending = (Test-Path "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending") -or
           (Test-Path "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired")
if ($pending) { Warn "A reboot is pending. Agent upgrades often wait for it." } else { Ok "No reboot pending" }

Section "Next steps"
"- Required service stopped: start it. If it will not start, collect logs with the Case Diagnostic Tool (CDT) from the Tools page."
"- Server not reachable: check firewall and proxy. Patterns stay old until the agent can reach the server."
"- Agent offline in the console but online here: check that the server can reach the agent listen port, or that the agent uses the cloud (SaaS) connection."
"- Patterns old but connected: run 'Update Now' from the agent tray icon and check the update source in the agent's update settings."
Stop-Transcript | Out-Null
Write-Host "`nReport saved to $report" -ForegroundColor Cyan
