# Used by bin/enchiridion.cmd to read "version" from plugin.json without
# embedding parentheses in a for /f backtick command (cmd.exe closes the
# in(...) clause on the first ) it sees, breaking any inline -Command string
# that contains method calls or array subscripts).

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Manifest
)

$ErrorActionPreference = "Stop"

$content = Get-Content -Raw $Manifest
$match = [regex]::Match($content, '"version"\s*:\s*"([^"]*)"')
if (-not $match.Success) {
    Write-Error "enchiridion: no `"version`" in $Manifest"
    exit 1
}
Write-Output $match.Groups[1].Value
