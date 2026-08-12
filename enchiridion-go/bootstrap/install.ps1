# Dependency-free bootstrap for the enchiridion Go binary (Windows).
#
# Lazy-fetches the platform binary from a GitHub Release into a
# version-namespaced cache directory, verifies its SHA256 checksum, and
# writes the resulting binary's absolute path to stdout. Diagnostic output
# goes to the information stream, so callers can safely do:
#
#   $bin = powershell -File bootstrap/install.ps1 -PluginRoot $PluginRoot -Version $Version
#
# Uses only Invoke-WebRequest and Get-FileHash — both built into Windows
# PowerShell since Windows 7 / PowerShell 3.0 — so this never requires
# Python or any other runtime (docs/adr/0013).

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$PluginRoot,
    [Parameter(Mandatory = $true)][string]$Version,
    [string]$Repo = "dhague/enchiridion"
)

$ErrorActionPreference = "Stop"

function Die($Message) {
    Write-Error "enchiridion bootstrap: $Message"
    exit 1
}

$NormalizedVersion = if ($Version.StartsWith("v")) { $Version } else { "v$Version" }

switch ($env:PROCESSOR_ARCHITECTURE) {
    "ARM64" { $GoArch = "arm64" }
    "AMD64" { $GoArch = "amd64" }
    default { Die "unsupported architecture '$env:PROCESSOR_ARCHITECTURE'" }
}

$CacheRoot = Join-Path -Path $PluginRoot -ChildPath ".enchiridion-cache"
$CacheDir = Join-Path -Path $CacheRoot -ChildPath $NormalizedVersion
$BinaryPath = Join-Path -Path $CacheDir -ChildPath "enchiridion.exe"
$Asset = "enchiridion_windows_${GoArch}.exe"
$BaseUrl = "https://github.com/$Repo/releases/download/$NormalizedVersion"

if (Test-Path $BinaryPath) {
    Write-Output $BinaryPath
    exit 0
}

Write-Information "enchiridion bootstrap: fetching $NormalizedVersion for windows/$GoArch" -InformationAction Continue

# This is a cache, not a rollback mechanism (docs/adr/0013) — drop every
# other version directory before fetching the one that's actually needed.
if (Test-Path $CacheRoot) {
    Get-ChildItem -Path $CacheRoot -Directory | Where-Object { $_.FullName -ne $CacheDir } | Remove-Item -Recurse -Force
}

New-Item -ItemType Directory -Path $CacheDir -Force | Out-Null
$TmpDir = Join-Path ([System.IO.Path]::GetTempPath()) ([System.IO.Path]::GetRandomFileName())
New-Item -ItemType Directory -Path $TmpDir -Force | Out-Null
$TmpBinary = Join-Path $TmpDir "enchiridion.exe"
$TmpChecksums = Join-Path $TmpDir "checksums.txt"

try {
    try {
        Invoke-WebRequest -Uri "$BaseUrl/$Asset" -OutFile $TmpBinary -UseBasicParsing
    }
    catch {
        Die "download failed for $Asset from $BaseUrl — check that $NormalizedVersion was released for windows/$GoArch ($_)"
    }

    try {
        Invoke-WebRequest -Uri "$BaseUrl/checksums.txt" -OutFile $TmpChecksums -UseBasicParsing
    }
    catch {
        Die "failed to download checksums.txt from $BaseUrl ($_)"
    }

    $ChecksumLine = Select-String -Path $TmpChecksums -Pattern "  $Asset$" | Select-Object -First 1
    if (-not $ChecksumLine) {
        Die "no checksum entry for $Asset in checksums.txt"
    }
    $ExpectedSha = ($ChecksumLine.Line -split "\s+")[0]

    $ActualSha = (Get-FileHash -Path $TmpBinary -Algorithm SHA256).Hash
    if ($ActualSha.ToLower() -ne $ExpectedSha.ToLower()) {
        Die "checksum mismatch for $Asset (expected $ExpectedSha, got $ActualSha) — download may be corrupt or tampered with"
    }

    # Windows SmartScreen (unsigned binary, docs/adr/0011) blocks execution
    # of files carrying the Zone.Identifier alternate-data-stream mark of
    # the web, not the file itself — strip it so first execution succeeds.
    Unblock-File -Path $TmpBinary -ErrorAction SilentlyContinue

    try {
        & $TmpBinary version | Out-Null
    }
    catch {
        Die "downloaded binary failed to execute — if Windows reports it as blocked by SmartScreen (enchiridion is not yet code-signed, see docs/adr/0011), right-click the file in Explorer, choose Properties, and check 'Unblock', or run: Unblock-File -Path '$TmpBinary'"
    }

    Move-Item -Path $TmpBinary -Destination $BinaryPath -Force
    Write-Output $BinaryPath
}
finally {
    Remove-Item -Path $TmpDir -Recurse -Force -ErrorAction SilentlyContinue
}
