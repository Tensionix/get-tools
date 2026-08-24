# Remove-From-Lists.ps1
# Shows all packages across config lists, then loops prompting for IDs to remove.
# ROOT is passed via environment variable by Remove-From-Lists.cmd.

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
    Write-Host "  Copy a package ID and paste it below to remove from all lists."
    Write-Host "  Press Enter without input to exit."
    Write-Host "----------------------------------------------------------------"
}

Show-AllPackages

while ($true) {
    Write-Host ""
    $id = (Read-Host "Remove").Trim()

    if ([string]::IsNullOrWhiteSpace($id)) {
        Write-Host "[INFO] Exiting." -ForegroundColor Gray
        break
    }

    $found = $false
    foreach ($file in $configFiles) {
        if (-not (Test-Path $file.Path)) { continue }
        $lines    = Get-Content $file.Path
        $newLines = $lines | Where-Object { $_.Trim() -ine $id }
        if ($lines.Count -ne $newLines.Count) {
            $newLines | Set-Content $file.Path -Encoding UTF8
            Write-Host "[OK] Removed from [$($file.Label)]" -ForegroundColor Green
            $found = $true
        }
    }

    if (-not $found) {
        Write-Host "[INFO] Not found in any config list: $id" -ForegroundColor Yellow
    }
}
