# Parameters are passed via environment variables (set by Launch-WinGet-Lists.cmd)
# to avoid Windows CMD quoting issues with paths containing spaces.

$Action    = $env:WINGET_ACTION
$Confirm   = ($env:WINGET_CONFIRM -eq '1')
$ListFiles = @()
foreach ($i in 1..4) {
    $v = [System.Environment]::GetEnvironmentVariable("WINGET_LIST_$i")
    if (![string]::IsNullOrEmpty($v)) { $ListFiles += $v }
}

if ([string]::IsNullOrEmpty($Action)) {
    Write-Host '[ERROR] WINGET_ACTION environment variable not set.'
    exit 1
}
if ($Action -notin @('install','update','pin')) {
    Write-Host "[ERROR] Invalid action: $Action. Must be install, update, or pin."
    exit 1
}
if ($ListFiles.Count -eq 0) {
    Write-Host '[ERROR] No WINGET_LIST_N environment variables set.'
    exit 1
}

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding

$Root = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$LogDir = Join-Path $Root 'logs'
if (-not (Test-Path -LiteralPath $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}

$Stamp = Get-Date -Format 'yyyy-MM-dd_HH-mm-ss'
$RunLog = Join-Path $LogDir ("{0}_{1}.log" -f $Action, $Stamp)
$FailList = Join-Path $LogDir ("last_failed_{0}.txt" -f $Action)
Set-Content -LiteralPath $FailList -Value '' -Encoding utf8

function Write-Log {
    param([string]$Message = '')
    Write-Host $Message
    Add-Content -LiteralPath $RunLog -Value $Message -Encoding utf8
}

function Test-WingetAvailable {
    return [bool](Get-Command winget -ErrorAction SilentlyContinue)
}

function Invoke-WingetCapture {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments,
        [int[]]$BenignExitCodes = @()
    )

    Write-Log ("[CMD] winget {0}" -f ($Arguments -join ' '))
    $output = (& winget @Arguments 2>&1 | Out-String)
    $exitCode = $LASTEXITCODE

    if ($output) {
        foreach ($line in ($output -split "`r?`n")) {
            if ($line -ne '') {
                Add-Content -LiteralPath $RunLog -Value $line -Encoding utf8
            }
        }
    }

    return [pscustomobject]@{
        ExitCode = $exitCode
        Output   = $output
        Benign   = ($BenignExitCodes -contains $exitCode)
        Success  = ($exitCode -eq 0)
    }
}

function Invoke-WingetDirect {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments,
        [int[]]$BenignExitCodes = @()
    )

    Write-Log ("[CMD] winget {0}" -f ($Arguments -join ' '))

    # Process.Start with UseShellExecute=false and no Redirect* flags:
    # the child inherits the parent's real stdin/stdout/stderr handles directly,
    # bypassing PowerShell's line-buffered pipeline. This lets winget render its
    # \r-based progress bar and full installer UI on screen.
    # (& winget @args buffers through PS pipeline and swallows the progress bar.)
    $argStr = ($Arguments | ForEach-Object {
        if ($_ -match '[\s"]') { '"' + $_.Replace('"', '""') + '"' } else { $_ }
    }) -join ' '
    $psi = [System.Diagnostics.ProcessStartInfo]::new('winget', $argStr)
    $psi.UseShellExecute = $false
    $proc = [System.Diagnostics.Process]::Start($psi)
    $proc.WaitForExit()
    $exitCode = $proc.ExitCode

    return [pscustomobject]@{
        ExitCode = $exitCode
        Benign   = ($BenignExitCodes -contains $exitCode)
        Success  = ($exitCode -eq 0)
    }
}

function Get-UserConfirm {
    param([string]$PackageId, [string]$Action)
    while ($true) {
        Write-Host ("[?] $Action '$PackageId'  [Enter/Y] Yes  [N] No  [Q] Quit: ") `
                   -NoNewline -ForegroundColor Cyan
        $key = $Host.UI.RawUI.ReadKey('NoEcho,IncludeKeyDown')
        if ($key.VirtualKeyCode -eq 13) {
            Write-Host 'Y'
            return 'Y'
        }
        $ch  = $key.Character.ToString().ToUpper()
        Write-Host $ch
        if ($ch -in @('Y','N','Q')) { return $ch }
    }
}

function Get-ListEntries {
    param([string[]]$Paths)

    $seen = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
    $result = New-Object 'System.Collections.Generic.List[string]'

    foreach ($path in $Paths) {
        if (-not (Test-Path -LiteralPath $path)) {
            Write-Log "[INFO] Skipping missing list: $path"
            continue
        }

        Write-Log "[STEP] Reading list: $path"
        foreach ($rawLine in (Get-Content -LiteralPath $path -Encoding UTF8)) {
            $line = $rawLine.Trim()
            if ([string]::IsNullOrWhiteSpace($line)) { continue }
            if ($line.StartsWith('#')) { continue }
            if ($seen.Add($line)) {
                [void]$result.Add($line)
            }
        }
    }

    return $result.ToArray()
}

function Test-PackageInstalled {
    param([string]$PackageId)

    Write-Log "[STEP] Check installed: $PackageId"
    $result = Invoke-WingetCapture -Arguments @(
        'list', '--id', $PackageId, '-e', '--accept-source-agreements', '--disable-interactivity'
    ) -BenignExitCodes @(-1978335212)

    return ($result.ExitCode -eq 0)
}

function Test-PackageUpgradeAvailable {
    param([string]$PackageId)

    Write-Log "[STEP] Check update availability: $PackageId"
    $result = Invoke-WingetCapture -Arguments @(
        'list', '--id', $PackageId, '-e', '--source', 'winget',
        '--accept-source-agreements', '--disable-interactivity',
        '--upgrade-available'
    ) -BenignExitCodes @(-1978335212)

    if ($result.ExitCode -ne 0) {
        return $false
    }

    return ($result.Output -match [regex]::Escape($PackageId))
}

function Test-PackagePinExists {
    param([string]$PackageId)

    Write-Log "[STEP] Check pin: $PackageId"
    $result = Invoke-WingetCapture -Arguments @(
        'pin', 'list', '--id', $PackageId, '-e', '--accept-source-agreements', '--disable-interactivity'
    ) -BenignExitCodes @(-1978335133)

    return ($result.ExitCode -eq 0)
}

function Invoke-InstallPackage {
    param([string]$PackageId)

    $args = @(
        'install', '--id', $PackageId, '-e', '--source', 'winget',
        '--accept-package-agreements', '--accept-source-agreements', '--no-upgrade'
    )

    Write-Log "[STEP] Install: $PackageId"
    return (Invoke-WingetDirect -Arguments $args -BenignExitCodes @(2))
}

function Invoke-UpdatePackage {
    param([string]$PackageId)

    if (-not (Test-PackageInstalled -PackageId $PackageId)) {
        Write-Log "[INFO] Not installed, skipping: $PackageId"
        return [pscustomobject]@{ ExitCode = 0; Benign = $true; Success = $true }
    }

    if (-not (Test-PackageUpgradeAvailable -PackageId $PackageId)) {
        Write-Log "[INFO] No update available: $PackageId"
        return [pscustomobject]@{ ExitCode = 0; Benign = $true; Success = $true }
    }

    $args = @(
        'upgrade', '--id', $PackageId, '-e', '--source', 'winget',
        '--accept-package-agreements', '--accept-source-agreements'
    )

    Write-Log "[STEP] Update: $PackageId"
    return (Invoke-WingetDirect -Arguments $args -BenignExitCodes @(2))
}

function Invoke-PinPackage {
    param([string]$PackageId)

    if (-not (Test-PackageInstalled -PackageId $PackageId)) {
        Write-Log "[INFO] Not installed, skipping pin: $PackageId"
        return [pscustomobject]@{ ExitCode = 0; Benign = $true; Success = $true }
    }

    if (Test-PackagePinExists -PackageId $PackageId) {
        Write-Log "[INFO] Pin already exists: $PackageId"
        return [pscustomobject]@{ ExitCode = 0; Benign = $true; Success = $true }
    }

    $args = @('pin', 'add', '--id', $PackageId, '-e', '--source', 'winget', '--blocking', '--accept-source-agreements')

    Write-Log "[STEP] Pin: $PackageId"
    return (Invoke-WingetDirect -Arguments $args -BenignExitCodes @(2))
}

if (-not (Test-WingetAvailable)) {
    Write-Log '[ERROR] winget was not found. Microsoft App Installer is required.'
    exit 1
}

Write-Log '================================================================'
Write-Log ("AUDION GET TOOLS {0}{1}" -f $Action.ToUpperInvariant(), $(if ($Confirm) {' [INTERACTIVE]'} else {''}))
Write-Log '================================================================'
Write-Log "[INFO] Run log: $RunLog"
Write-Log "[INFO] Fail list: $FailList"
Write-Log ''
Write-Log ("[INFO] winget version: {0}" -f ((winget --version | Out-String).Trim()))

$packages = Get-ListEntries -Paths $ListFiles
$failed = 0
$processed = 0

foreach ($packageId in $packages) {
    Write-Log ''
    Write-Log '----------------------------------------------------------------'
    Write-Log ("[ITEM] {0}" -f $packageId)

    if ($Confirm) {
        $answer = Get-UserConfirm -PackageId $packageId -Action $Action
        if ($answer -eq 'Q') {
            Write-Log '[INFO] Quit requested by user.'
            break
        }
        if ($answer -eq 'N') {
            Write-Log "[SKIP] $packageId"
            continue
        }
    }

    $processed++

    try {
        switch ($Action) {
            'install' { $result = Invoke-InstallPackage -PackageId $packageId }
            'update'  { $result = Invoke-UpdatePackage -PackageId $packageId }
            'pin'     { $result = Invoke-PinPackage -PackageId $packageId }
        }

        if ($result.Success -or $result.Benign) {
            if ($result.ExitCode -eq 2) {
                Write-Log "[CANCELLED] $packageId"
            } else {
                Write-Log "[OK] $packageId"
            }
            continue
        }

        $failed++
        Write-Log ("[WARN] Failed: {0}  exit={1}" -f $packageId, $result.ExitCode)
        Add-Content -LiteralPath $FailList -Value $packageId -Encoding utf8
    }
    catch {
        $failed++
        Write-Log ("[WARN] Failed: {0}" -f $packageId)
        Write-Log ("[WARN] {0}" -f $_.Exception.Message)
        Add-Content -LiteralPath $FailList -Value $packageId -Encoding utf8
    }
}

Write-Log ''
Write-Log ("[INFO] Total package lines processed: {0}" -f $processed)
Write-Log ("[INFO] Failed package lines: {0}" -f $failed)

if ($failed -eq 0) {
    Write-Log '[OK] Completed without package failures.'
    exit 0
}

Write-Log '[WARN] Some packages failed. See the fail list for details.'
exit 1
