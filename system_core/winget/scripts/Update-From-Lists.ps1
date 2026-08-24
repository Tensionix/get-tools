# Update-From-Lists.ps1
# Shows all packages across config lists, then loops prompting for IDs to update via winget.
# ROOT is passed via environment variable by Update-From-Lists.cmd.

$root = $env:ROOT
if (-not $root) {
    Write-Host "[ERROR] ROOT environment variable not set." -ForegroundColor Red
    exit 1
}

$configFiles = @(
    [PSCustomObject]@{ Label = "system";              Path = "${root}config\system.txt" }
    [PSCustomObject]@{ Label = "dev";                 Path = "${root}config\dev.txt" }
    [PSCustomObject]@{ Label = "ai";                  Path = "${root}config\ai.txt" }
    [PSCustomObject]@{ Label = "pkms";                Path = "${root}config\pkms.txt" }
    [PSCustomObject]@{ Label = "office";              Path = "${root}config\office.txt" }
    [PSCustomObject]@{ Label = "media";               Path = "${root}config\media.txt" }
    [PSCustomObject]@{ Label = "browsers-vpn";        Path = "${root}config\browsers-vpn.txt" }
    [PSCustomObject]@{ Label = "hardware-benchmarks"; Path = "${root}config\hardware-benchmarks.txt" }
    [PSCustomObject]@{ Label = "custom";              Path = "${root}config\custom.txt" }
    [PSCustomObject]@{ Label = "pins";                Path = "${root}config\pins.txt" }
)

function Show-AllPackages {
    Write-Host ""
    Write-Host "================================================================" -ForegroundColor Cyan
    Write-Host "  PACKAGES IN ALL CONFIG LISTS" -ForegroundColor Cyan
    Write-Host "================================================================" -ForegroundColor Cyan

    foreach ($file in $configFiles) {
        if (-not (Test-Path $file.Path)) { continue }
        $packages = Get-Content $file.Path |
            Where-Object { $_ -notmatch '^\s*#' -and $_ -match '\S' }
        if ($packages.Count -eq 0) { continue }
        Write-Host ""
        Write-Host "  [$($file.Label)]" -ForegroundColor Yellow
        foreach ($pkg in $packages) {
            Write-Host "    $pkg"
        }
    }

    Write-Host ""
    Write-Host "----------------------------------------------------------------"
    Write-Host "  Copy a package ID and paste it below to update."
    Write-Host "  Press Enter without input to exit."
    Write-Host "----------------------------------------------------------------"
}

function Invoke-WingetDirect {
    param([string[]]$Arguments)
    $psi = [System.Diagnostics.ProcessStartInfo]::new("winget")
    $psi.Arguments       = $Arguments -join " "
    $psi.UseShellExecute = $false
    $proc = [System.Diagnostics.Process]::Start($psi)
    $proc.WaitForExit()
    return $proc.ExitCode
}

Show-AllPackages

while ($true) {
    Write-Host ""
    $id = (Read-Host "Update").Trim()

    if ([string]::IsNullOrWhiteSpace($id)) {
        Write-Host "[INFO] Exiting." -ForegroundColor Gray
        break
    }

    Write-Host ""
    Write-Host "----------------------------------------------------------------"
    Write-Host "[INFO] Updating: $id" -ForegroundColor Cyan
    Write-Host "----------------------------------------------------------------"
    Write-Host ""

    $rc = Invoke-WingetDirect @(
        "upgrade", "--id", "`"$id`"", "-e",
        "--source", "winget",
        "--accept-source-agreements",
        "--accept-package-agreements"
    )

    Write-Host ""
    switch ($rc) {
        0             { Write-Host "[OK] Updated: $id" -ForegroundColor Green }
        2             { Write-Host "[CANCELLED] Update was cancelled." -ForegroundColor Yellow }
        -1978335212   { Write-Host "[INFO] No update available: $id" -ForegroundColor Gray }
        -1978335189   { Write-Host "[INFO] Package not found: $id" -ForegroundColor Yellow }
        -1978335136   { Write-Host "[INFO] Already up to date: $id" -ForegroundColor Gray }
        -1978335135   { Write-Host "[WARN] Requires reboot or elevation: $id" -ForegroundColor Yellow }
        default       { Write-Host "[WARN] winget returned exit code $rc for: $id" -ForegroundColor Yellow }
    }
}
