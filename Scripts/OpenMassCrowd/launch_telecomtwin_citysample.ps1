[CmdletBinding()]
param(
    [string]$UnrealRoot = "D:\astrea\UE_5.7",
    [string]$Map = "/Game/Maps/shanghai",
    [ValidateRange(2, 16)]
    [int]$CoreLimit = 4,
    [string]$UserDir = "D:\TelecomTwinCitySampleUser",
    [string]$LocalDataCachePath = "D:\TelecomTwinCache\DDC",
    [string]$ZenDataPath = "D:\TelecomTwinCache\Zen"
)

$ErrorActionPreference = "Stop"

$editor = Join-Path $UnrealRoot "Engine\Binaries\Win64\UnrealEditor.exe"
$project = (Resolve-Path (Join-Path $PSScriptRoot "..\..\TelecomTwin.uproject")).Path
$projectRoot = Split-Path -Parent $project
$citySampleBlueprint = Join-Path `
    $projectRoot `
    "Content\CitySampleCrowd\Blueprints\BP_CrowdCharacter.uasset"

if (-not (Test-Path -LiteralPath $editor)) {
    throw "UnrealEditor.exe was not found: $editor"
}

if (-not (Test-Path -LiteralPath $citySampleBlueprint -PathType Leaf)) {
    throw @"
Epic City Sample Crowds is not mounted. Expected:
$citySampleBlueprint

Acquire the UE-Only Fab content with the collaborator's own Epic account, then run:
pwsh -ExecutionPolicy Bypass -File .\Scripts\OpenMassCrowd\link_city_sample_crowds.ps1 -Source <CitySampleCrowd content directory>
"@
}

$runningEditor = Get-Process -Name "UnrealEditor" -ErrorAction SilentlyContinue
if ($runningEditor) {
    throw "Unreal Editor is already running. Close it before using this launcher."
}

New-Item -ItemType Directory -Path $UserDir -Force | Out-Null
New-Item -ItemType Directory -Path $LocalDataCachePath -Force | Out-Null
New-Item -ItemType Directory -Path $ZenDataPath -Force | Out-Null

# The file-system Local DDC node in UE 5.7 reads this environment override.
Set-Item -Path "Env:UE-LocalDataCachePath" -Value $LocalDataCachePath
Set-Item -Path "Env:UE-ZenDataPath" -Value $ZenDataPath

# City Sample contains 4K/8K virtual textures. The D-drive DDC prevents C-drive
# Zen cache failures, while the core limit keeps first-run texture builds from
# allocating several multi-gigabyte compression jobs at the same time.
$arguments = @(
    $project,
    $Map,
    "-UserDir=$UserDir",
    "-LocalDataCachePath=$LocalDataCachePath",
    "-ZenDataPath=$ZenDataPath",
    "-corelimit=$CoreLimit",
    "-asynctexturecompilationmaxconcurrency=1",
    "-asyncassetcompilationmaxconcurrency=1",
    "-ini:Engine:[ConsoleVariables]:Editor.AsyncAssetCompilationMaxMemoryUsage=4",
    "-cefdebug=9222",
    "-log"
)

$process = Start-Process `
    -FilePath $editor `
    -ArgumentList $arguments `
    -WorkingDirectory $projectRoot `
    -WindowStyle Normal `
    -PassThru

[pscustomobject]@{
    Id = $process.Id
    StartTime = $process.StartTime
    Path = $process.Path
    Status = "Editor launched; pedestrians are created only while PIE is running."
    NextStep = "Wait for the shanghai/Cesium view, then press Alt+P and allow 8-15 seconds for City Sample characters."
    ExpectedLog = "OPEN_MASS_CROWD_READY requested=30 spawned=30"
}
