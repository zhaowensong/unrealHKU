[CmdletBinding()]
param(
    [string]$UnrealRoot = "",
    [string]$Map = "/Game/Maps/shanghai",
    [ValidateRange(2, 16)]
    [int]$CoreLimit = 8,
    [string]$UserDir = "",
    [string]$LocalDataCachePath = "",
    [string]$ZenDataPath = "",
    [string]$TempPath = ""
)

$ErrorActionPreference = "Stop"

$project = (Resolve-Path (Join-Path $PSScriptRoot "..\..\TelecomTwin.uproject")).Path
$projectRoot = Split-Path -Parent $project
$engineAssociation = (
    Get-Content -LiteralPath $project -Raw | ConvertFrom-Json
).EngineAssociation

if ([string]::IsNullOrWhiteSpace($UnrealRoot)) {
    $candidates = [System.Collections.Generic.List[string]]::new()
    if (-not [string]::IsNullOrWhiteSpace($env:TELECOMTWIN_UNREAL_ROOT)) {
        $candidates.Add($env:TELECOMTWIN_UNREAL_ROOT)
    }
    $launcherRegistryPath =
        "HKLM:\SOFTWARE\EpicGames\Unreal Engine\$engineAssociation"
    if (Test-Path -LiteralPath $launcherRegistryPath) {
        $installedDirectory = (
            Get-ItemProperty -LiteralPath $launcherRegistryPath
        ).InstalledDirectory
        if ($installedDirectory) {
            $candidates.Add($installedDirectory)
        }
    }
    $sourceBuildsRegistryPath =
        "HKCU:\SOFTWARE\Epic Games\Unreal Engine\Builds"
    if (Test-Path -LiteralPath $sourceBuildsRegistryPath) {
        $sourceBuilds = Get-ItemProperty -LiteralPath $sourceBuildsRegistryPath
        foreach ($property in $sourceBuilds.PSObject.Properties) {
            if ($property.Name -notlike "PS*") {
                $candidates.Add([string]$property.Value)
            }
        }
    }
    foreach ($programFilesRoot in @(
        $env:ProgramW6432,
        $env:ProgramFiles,
        ${env:ProgramFiles(x86)}
    )) {
        if (-not [string]::IsNullOrWhiteSpace($programFilesRoot)) {
            $candidates.Add((
                Join-Path $programFilesRoot "Epic Games\UE_$engineAssociation"
            ))
        }
    }
    foreach ($candidate in $candidates) {
        $candidateEditor = Join-Path `
            $candidate `
            "Engine\Binaries\Win64\UnrealEditor.exe"
        if (Test-Path -LiteralPath $candidateEditor -PathType Leaf) {
            $UnrealRoot = (Resolve-Path -LiteralPath $candidate).Path
            break
        }
    }
}

if ([string]::IsNullOrWhiteSpace($UnrealRoot)) {
    throw @"
找不到与项目匹配的 Unreal Engine $engineAssociation。
请安装对应引擎，或设置 TELECOMTWIN_UNREAL_ROOT 环境变量。
"@
}

$portableCacheRoot = ""
if (-not [string]::IsNullOrWhiteSpace($env:TELECOMTWIN_DEMO_CACHE_ROOT)) {
    $portableCacheRoot = $env:TELECOMTWIN_DEMO_CACHE_ROOT
}
else {
    $projectVolumeRoot = [System.IO.Path]::GetPathRoot($projectRoot)
    if (-not [string]::IsNullOrWhiteSpace($projectVolumeRoot)) {
        $volumeCache = Join-Path $projectVolumeRoot "TelecomTwinDemoCache"
        try {
            New-Item -ItemType Directory -Path $volumeCache -Force | Out-Null
            $portableCacheRoot = $volumeCache
        }
        catch {
            Write-Verbose "Project volume cache is unavailable: $_"
        }
    }
}
if ([string]::IsNullOrWhiteSpace($portableCacheRoot)) {
    $localAppData = [Environment]::GetFolderPath("LocalApplicationData")
    if ([string]::IsNullOrWhiteSpace($localAppData)) {
        throw "无法确定可写的演示缓存目录。"
    }
    $portableCacheRoot = Join-Path $localAppData "TelecomTwinDemoCache"
}
if ([string]::IsNullOrWhiteSpace($UserDir)) {
    $UserDir = Join-Path $portableCacheRoot "User"
}
if ([string]::IsNullOrWhiteSpace($LocalDataCachePath)) {
    $LocalDataCachePath = Join-Path $portableCacheRoot "DDC"
}
if ([string]::IsNullOrWhiteSpace($ZenDataPath)) {
    $ZenDataPath = Join-Path $portableCacheRoot "Zen"
}
if ([string]::IsNullOrWhiteSpace($TempPath)) {
    $TempPath = Join-Path $portableCacheRoot "Temp"
}

$editor = Join-Path $UnrealRoot "Engine\Binaries\Win64\UnrealEditor.exe"
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

# City Sample contains 4K/8K virtual textures. A short cache dynamically chosen
# from the project volume avoids fixed drive assumptions and UE's path limit.
# Texture and asset compilation remain single-concurrency,
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
