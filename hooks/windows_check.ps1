# Scheduled wrapper for scripts.run_windows (2026-09-04). The task used to run the
# script bare in a console that closed instantly, which made the daily check useless.
# This wrapper (a) appends the full report to data\windows_check.log so every run is
# reviewable, and (b) pops a persistent message box ONLY when something needs a human:
# a MISSED window, an OPEN window inside ~a day of its deadline, a FLAG/RELEASE line,
# or STALE freshness. Quiet days stay quiet.
#
# Task Scheduler action (set once):
#   powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden
#     -File "<repo>\hooks\windows_check.ps1"
# Run with -Quiet to log without ever showing the popup (used for testing).
param([switch]$Quiet)

$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

$logPath = Join-Path $repo "data\windows_check.log"
if ((Test-Path $logPath) -and ((Get-Item $logPath).Length -gt 1MB)) {
    Move-Item -Force $logPath ($logPath + ".old")
}

$output = & py -3.10 -m scripts.run_windows 2>&1 | Out-String
$stamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
Add-Content -Path $logPath -Encoding utf8 -Value ("=== $stamp ===`r`n" + $output)

$attention = $false
if ($output -match 'MISSED|FLAG:|RELEASE:|STALE') { $attention = $true }
foreach ($m in [regex]::Matches($output, 'OPEN.*\((\d+(\.\d+)?) h remaining\)')) {
    if ([double]$m.Groups[1].Value -le 26.0) { $attention = $true }
}

if ($attention -and -not $Quiet) {
    Add-Type -AssemblyName System.Windows.Forms
    [void][System.Windows.Forms.MessageBox]::Show(
        $output.Trim(),
        "Fantasy canonical windows -- attention needed",
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Information)
}
