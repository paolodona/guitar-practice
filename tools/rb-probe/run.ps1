# Run the Rubber Band WASM AudioWorklet probe and print the results.
#
#   powershell -ExecutionPolicy Bypass -File .\run.ps1            # both suites
#   powershell -ExecutionPolicy Bypass -File .\run.ps1 -Suite realtime
#
# The realtime suite takes about 70 seconds of wall clock, because it is measuring
# whether the worklet keeps up in real time — there is no way to hurry that.

param(
  [ValidateSet('offline', 'realtime', 'both')]
  [string]$Suite = 'both',
  [int]$Port = 8731
)

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

if (-not (Test-Path (Join-Path $here 'rubberband.wasm'))) {
  Write-Output 'rubberband.wasm not present; running fetch-wasm.ps1'
  & (Join-Path $here 'fetch-wasm.ps1')
}

$chrome = @(
  "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
  "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
  "$env:ProgramFiles (x86)\Microsoft\Edge\Application\msedge.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $chrome) { throw 'no Chrome or Edge found; the probe needs a Chromium AudioWorklet' }

$existing = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($existing) { throw "port $Port is already in use by pid $($existing.OwningProcess)" }

$server = Start-Process node -ArgumentList (Join-Path $here 'serve.js'), $Port -PassThru -NoNewWindow
Start-Sleep -Milliseconds 500

$suites = @()
if ($Suite -eq 'both' -or $Suite -eq 'offline')  { $suites += ,@('offline.html',  'results.json') }
if ($Suite -eq 'both' -or $Suite -eq 'realtime') { $suites += ,@('realtime.html', 'results-rt.json') }

try {
  foreach ($s in $suites) {
    $page = $s[0]; $outFile = Join-Path $here $s[1]
    if (Test-Path $outFile) { Remove-Item $outFile }
    Write-Output "running $page"
    $profileDir = Join-Path $env:TEMP ("rb-probe-" + [guid]::NewGuid().ToString('N'))
    $browser = Start-Process $chrome -PassThru -ArgumentList `
      '--headless=new', '--disable-gpu', '--autoplay-policy=no-user-gesture-required',
      "--user-data-dir=$profileDir", "http://127.0.0.1:$Port/$page"

    $deadline = (Get-Date).AddMinutes(5)
    while (-not (Test-Path $outFile) -and (Get-Date) -lt $deadline) { Start-Sleep -Seconds 2 }
    Stop-Process -Id $browser.Id -Force -ErrorAction SilentlyContinue
    Remove-Item -Recurse -Force $profileDir -ErrorAction SilentlyContinue

    if (-not (Test-Path $outFile)) { throw "$page produced no results inside five minutes" }
    $r = Get-Content $outFile -Raw | ConvertFrom-Json
    if (-not $r.ok) { throw "$page failed: $($r.error)" }
    foreach ($run in $r.runs) { Write-Output ("  " + ($run | ConvertTo-Json -Compress)) }
  }
} finally {
  Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue
}

Write-Output 'done; results.json / results-rt.json written'
