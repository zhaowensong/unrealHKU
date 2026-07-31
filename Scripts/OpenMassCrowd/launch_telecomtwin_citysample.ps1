[CmdletBinding()]
param(
    [string]$UnrealRoot = "D:\astrea\UE_5.7",
    [string]$Map = "/Game/Maps/shanghai",
    [ValidateRange(2, 16)]
    [int]$CoreLimit = 8,
    [string]$UserDir = "D:\TelecomTwinCitySampleUser",
    [string]$LocalDataCachePath = "D:\TelecomTwinCache\DDC",
    [string]$ZenDataPath = "D:\TelecomTwinCache\Zen",
    [string]$TempPath = "D:\TelecomTwinTemp"
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
New-Item -ItemType Directory -Path $TempPath -Force | Out-Null

# The file-system Local DDC node in UE 5.7 reads this environment override.
Set-Item -Path "Env:UE-LocalDataCachePath" -Value $LocalDataCachePath
Set-Item -Path "Env:UE-ZenDataPath" -Value $ZenDataPath
Set-Item -Path "Env:TEMP" -Value $TempPath
Set-Item -Path "Env:TMP" -Value $TempPath

# City Sample contains 4K/8K virtual textures. The D-drive DDC prevents C-drive
# Zen cache failures. Texture and asset compilation remain single-concurrency,
# while the eight-core runtime limit leaves enough CPU headroom for 100 Mass
# pedestrians on the 16-core reference workstation.
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
    NextStep = "Wait for the shanghai/Cesium view, then press Alt+P and allow about 40-50 seconds for collision-certified crowd startup."
    ExpectedLog = "OPEN_MASS_CROWD_READY requested=100 spawned=100"
    TempPath = $TempPath
}
