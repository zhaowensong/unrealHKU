[CmdletBinding()]
param(
    [string]$Source = 'D:\CitySampleCrowds_Staging\Content\CitySampleCrowd',
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
)

$ErrorActionPreference = 'Stop'
$sourcePath = (Resolve-Path -LiteralPath $Source).Path
$contentPath = Join-Path $ProjectRoot 'Content'
$destinationPath = Join-Path $contentPath 'CitySampleCrowd'
$requiredAsset = Join-Path $sourcePath 'Blueprints\BP_CrowdCharacter.uasset'

if (-not (Test-Path -LiteralPath $requiredAsset -PathType Leaf)) {
    throw "The official BP_CrowdCharacter asset was not found under: $sourcePath"
}

if (Test-Path -LiteralPath $destinationPath) {
    $existing = Get-Item -LiteralPath $destinationPath -Force
    if (-not ($existing.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "Refusing to replace an existing non-link directory: $destinationPath"
    }

    $target = @($existing.Target)[0]
    if (-not $target) {
        throw "The existing reparse point has no readable target: $destinationPath"
    }

    $resolvedTarget = (Resolve-Path -LiteralPath $target).Path
    if ($resolvedTarget -ne $sourcePath) {
        throw "The existing link targets '$resolvedTarget', expected '$sourcePath'."
    }
}
else {
    New-Item -ItemType Junction -Path $destinationPath -Target $sourcePath | Out-Null
}

$mountedAsset = Join-Path $destinationPath 'Blueprints\BP_CrowdCharacter.uasset'
if (-not (Test-Path -LiteralPath $mountedAsset -PathType Leaf)) {
    throw "City Sample Crowds mount verification failed: $mountedAsset"
}

[pscustomobject]@{
    Status = 'Ready'
    Source = $sourcePath
    Mount = $destinationPath
    Blueprint = $mountedAsset
}
