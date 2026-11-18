<#
.SYNOPSIS
  Vision One endpoint agent (XDR sensor / Endpoint Basecamp) health check for Windows (read-only).
.DESCRIPTION
  Finds the Trend Micro services and their state, the agent folders and versions, tests the connection
  to the Vision One region (TCP, proxy, and whether a proxy or firewall is inspecting TLS), and lists
  recent errors. Changes nothing on the machine. Report: %TEMP%\XDR-Health-<computer>-<time>.txt
.PARAMETER Region
  Vision One region of the tenant: in, sg, us, eu, jp, au, mea (default in).
.PARAMETER Hosts
  Extra host names to test, for example the ones listed in the console's agent connection requirements.
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\xdr-sensor-health-windows.ps1 -Region sg
#>
param(
  [ValidateSet("in", "sg", "us", "eu", "jp", "au", "mea")][string]$Region = "in",
  [string[]]$Hosts = @()
)
$ErrorActionPreference = "SilentlyContinue"
$report = Join-Path $env:TEMP ("XDR-Health-{0}-{1:yyyyMMdd-HHmm}.txt" -f $env:COMPUTERNAME, (Get-Date))
Start-Transcript -Path $report | Out-Null
function Section($t) { Write-Host ""; Write-Host ("=== " + $t + " ===") -ForegroundColor Cyan }
function Ok($t)   { Write-Host ("[ OK ] " + $t) -ForegroundColor Green }
function Bad($t)  { Write-Host ("[FAIL] " + $t) -ForegroundColor Red }
function Warn($t) { Write-Host ("[WARN] " + $t) -ForegroundColor Yellow }

Section "Machine"
$os = Get-CimInstance Win32_OperatingSystem
"{0}  {1} build {2}  last boot {3}" -f $env:COMPUTERNAME, $os.Caption, $os.BuildNumber, $os.LastBootUpTime
"PowerShell {0}, TLS 1.2 enabled for .NET: {1}" -f $PSVersionTable.PSVersion, ([Net.ServicePointManager]::SecurityProtocol -match "Tls12")

Section "Trend Micro services"
$svc = Get-Service | Where-Object { $_.DisplayName -match "Trend Micro|Basecamp|Endpoint Sensor|Vision One" } | Sort-Object DisplayName
if (-not $svc) { Bad "No Trend Micro services found. The agent is not installed or was removed." }
foreach ($s in $svc) {
  if ($s.Status -eq "Running") { Ok "$($s.DisplayName) [$($s.Name)]" }
  elseif ($s.StartType -eq "Disabled") { Warn "$($s.DisplayName) [$($s.Name)] is disabled" }
  else { Bad "$($s.DisplayName) [$($s.Name)] is $($s.Status) (start type $($s.StartType))" }
}

Section "Agent folders and versions"
$roots = "${env:ProgramFiles}\Trend Micro", "${env:ProgramFiles(x86)}\Trend Micro"
foreach ($r in $roots) {
  Get-ChildItem $r -Directory | ForEach-Object {
    $exe = Get-ChildItem $_.FullName -Filter *.exe | Where-Object { $_.VersionInfo.ProductVersion } | Select-Object -First 1
    "  {0}  {1}" -f $_.FullName, $(if ($exe) { "version " + $exe.VersionInfo.ProductVersion } else { "" })
  }
}
Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*", "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*" |
  Where-Object { $_.Publisher -match "Trend Micro" } | Sort-Object DisplayName |
  ForEach-Object { "  installed: {0} {1}" -f $_.DisplayName, $_.DisplayVersion }

Section "Connection to Vision One ($Region)"
$suffix = if ($Region -eq "us") { "" } else { ".$Region" }
$targets = @("portal$suffix.xdr.trendmicro.com", "api$suffix.xdr.trendmicro.com") + $Hosts
foreach ($h in $targets) {
  $dns = Resolve-DnsName $h -ErrorAction SilentlyContinue | Where-Object { $_.IPAddress } | Select-Object -First 1
  if (-not $dns) { Bad "$h does not resolve (DNS)"; continue }
  $t = Test-NetConnection -ComputerName $h -Port 443 -WarningAction SilentlyContinue
  if (-not $t.TcpTestSucceeded) { Bad "$h`:443 NOT reachable ($($dns.IPAddress))"; continue }
  # Who signed the certificate? A company proxy or firewall that inspects TLS shows its own issuer here, and the agent will not trust it.
  try {
    $tcp = New-Object Net.Sockets.TcpClient($h, 443)
    $ssl = New-Object Net.Security.SslStream($tcp.GetStream(), $false, { $true })
    $ssl.AuthenticateAsClient($h)
    $issuer = ([Security.Cryptography.X509Certificates.X509Certificate2]$ssl.RemoteCertificate).Issuer
    $tcp.Close()
    if ($issuer -match "DigiCert|Amazon|Trend|GlobalSign|Sectigo|Let's Encrypt|Microsoft") { Ok "$h`:443 reachable, certificate from $issuer" }
    else { Warn "$h`:443 reachable but the certificate is from '$issuer'. TLS inspection is likely; ask for a bypass for Trend hosts." }
  } catch { Warn "$h`:443 reachable but the TLS handshake failed: $($_.Exception.Message)" }
}
"WinHTTP proxy:"; netsh winhttp show proxy | Select-String -Pattern "Proxy|Direct" | ForEach-Object { "  " + $_.Line.Trim() }
$ie = Get-ItemProperty "HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings"
if ($ie.ProxyEnable -eq 1) { "  User proxy: $($ie.ProxyServer)" }

Section "Time"
"Local time {0}, time source {1}" -f (Get-Date), (w32tm /query /source 2>&1)
w32tm /query /status 2>&1 | Select-String "Last Successful|Source" | ForEach-Object { "  " + $_.Line.Trim() }

Section "Trend errors in the last 3 days"
$ev = Get-WinEvent -FilterHashtable @{ LogName = "Application", "System"; Level = 1, 2; StartTime = (Get-Date).AddDays(-3) } |
  Where-Object { $_.ProviderName -match "Trend|Basecamp|TMBM|tmevt" -or $_.Message -match "Trend Micro" } | Select-Object -First 15
if ($ev) { $ev | ForEach-Object { "{0:yyyy-MM-dd HH:mm}  {1}  {2}" -f $_.TimeCreated, $_.ProviderName, ($_.Message -split "`n")[0] } } else { Ok "No Trend errors" }

Section "Disk"
Get-PSDrive -PSProvider FileSystem | Where-Object { $_.Used -ne $null } | ForEach-Object {
  $g = [math]::Round($_.Free / 1GB, 1); if ($g -lt 2) { Bad "$($_.Name): only $g GB free" } else { Ok "$($_.Name): $g GB free" } }

Section "Next steps"
"- Host not reachable or TLS inspected: allow the Vision One hosts for this region on the firewall or proxy, without TLS inspection."
"- A service stopped: start it. If it keeps stopping, collect logs with the agent's diagnostic tool and open a case."
"- Agent shows offline in Vision One but everything here is OK: check that the time is correct and the proxy allows the system account, not only users."
Stop-Transcript | Out-Null
Write-Host "`nReport saved to $report" -ForegroundColor Cyan
