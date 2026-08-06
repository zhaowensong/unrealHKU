[CmdletBinding()]
param(
    [string]$UnrealRoot = "",
    [string]$CacheRoot = "",
    [ValidateRange(1, 100)]
    [int]$ExpectedPopulation = 100,
    [ValidateRange(30, 600)]
    [int]$EditorTimeoutSeconds = 180,
    [ValidateRange(60, 900)]
    [int]$DemoTimeoutSeconds = 240,
    [switch]$Detached
)

$ErrorActionPreference = "Stop"
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$project = Join-Path $projectRoot "TelecomTwin.uproject"
$launcher = Join-Path $PSScriptRoot "launch_telecomtwin_citysample.ps1"
$orchestrator = Join-Path $PSScriptRoot "orchestrate_investor_delivery_demo.py"
$statusOutput = Join-Path `
    $projectRoot `
    "Saved\InvestorDeliveryDemo\one_click_demo_status.json"

function Resolve-TelecomTwinUnrealRoot {
    param([string]$RequestedRoot)

    $candidates = [System.Collections.Generic.List[string]]::new()
    if (-not [string]::IsNullOrWhiteSpace($RequestedRoot)) {
        $candidates.Add($RequestedRoot)
    }
    if (-not [string]::IsNullOrWhiteSpace($env:TELECOMTWIN_UNREAL_ROOT)) {
        $candidates.Add($env:TELECOMTWIN_UNREAL_ROOT)
    }

    $engineAssociation = "5.7"
    try {
        $engineAssociation = (
            Get-Content -LiteralPath $project -Raw | ConvertFrom-Json
        ).EngineAssociation
    }
    catch {
        Write-Verbose "Unable to read EngineAssociation: $_"
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

    $candidates.Add("D:\astrea\UE_5.7")
    $candidates.Add("C:\Program Files\Epic Games\UE_5.7")
    foreach ($candidate in $candidates) {
        if ([string]::IsNullOrWhiteSpace($candidate)) {
            continue
        }
        $editor = Join-Path $candidate "Engine\Binaries\Win64\UnrealEditor.exe"
        $python = Join-Path `
            $candidate `
            "Engine\Binaries\ThirdParty\Python3\Win64\python.exe"
        if ((Test-Path -LiteralPath $editor -PathType Leaf) -and
            (Test-Path -LiteralPath $python -PathType Leaf)) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }

    throw @"
找不到兼容的 Unreal Engine 5.7。
请安装 UE 5.7，或设置环境变量：
TELECOMTWIN_UNREAL_ROOT=D:\path\to\UE_5.7
"@
}

try {
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host " TelecomTwin 香港中环 100 人一键演示" -ForegroundColor Cyan
    Write-Host "============================================================" -ForegroundColor Cyan

    $resolvedUnrealRoot = Resolve-TelecomTwinUnrealRoot $UnrealRoot
    $unrealPython = Join-Path `
        $resolvedUnrealRoot `
        "Engine\Binaries\ThirdParty\Python3\Win64\python.exe"
    Write-Host "[环境] UE: $resolvedUnrealRoot"
    Write-Host "[环境] 项目: $projectRoot"

    if ([string]::IsNullOrWhiteSpace($CacheRoot)) {
        if (Test-Path -LiteralPath "D:\") {
            $CacheRoot = "D:\TelecomTwinDemoCache"
        }
        else {
            $CacheRoot = Join-Path $env:LOCALAPPDATA "TelecomTwinDemoCache"
        }
    }
    $cacheRootFull = [System.IO.Path]::GetFullPath($CacheRoot)
    $userDir = Join-Path $cacheRootFull "User"
    $ddcDir = Join-Path $cacheRootFull "DDC"
    $zenDir = Join-Path $cacheRootFull "Zen"
    $tempDir = Join-Path $cacheRootFull "Temp"

    $runningEditors = @(
        Get-CimInstance Win32_Process -Filter "Name='UnrealEditor.exe'" |
            Where-Object { $_.ExecutablePath }
    )
    if ($runningEditors.Count -gt 1) {
        throw "检测到多个 Unreal Editor 实例。请全部关闭后重新双击启动文件。"
    }
    if ($runningEditors.Count -eq 0) {
        Write-Host "[启动] 正在启动 TelecomTwin Unreal Editor……" -ForegroundColor Yellow
        & $launcher `
            -UnrealRoot $resolvedUnrealRoot `
            -CoreLimit 8 `
            -UserDir $userDir `
            -LocalDataCachePath $ddcDir `
            -ZenDataPath $zenDir `
            -TempPath $tempDir | Format-List
    }
    else {
        Write-Host (
            "[复用] 检测到一个 Unreal Editor（PID {0}），将验证是否为 TelecomTwin。" -f
                $runningEditors[0].ProcessId
        ) -ForegroundColor Yellow
    }

    # The UE Editor intentionally drops to roughly 3 FPS when it is not the
    # foreground window. Put it in front before the readiness measurement;
    # the progress console remains available behind it if a failure occurs.
    $editorFocusDeadline = (Get-Date).AddSeconds(30)
    $editorProcess = $null
    $editorFocused = $false
    $shell = New-Object -ComObject WScript.Shell
    while (-not $editorFocused -and (Get-Date) -lt $editorFocusDeadline) {
        $editorProcess = Get-Process -Name "UnrealEditor" -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($editorProcess) {
            $editorFocused = $shell.AppActivate($editorProcess.Id)
        }
        if (-not $editorFocused) {
            Start-Sleep -Seconds 1
        }
    }
    if (-not $editorFocused) {
        Write-Warning "UE 窗口尚未前置；将继续启动并依靠超时保护。"
    }

    if ($Detached) {
        $unrealPythonWindowless = Join-Path `
            $resolvedUnrealRoot `
            "Engine\Binaries\ThirdParty\Python3\Win64\pythonw.exe"
        $detachedArguments = @(
            ('"{0}"' -f $orchestrator),
            "--expected-population", $ExpectedPopulation,
            "--editor-timeout", $EditorTimeoutSeconds,
            "--demo-timeout", $DemoTimeoutSeconds,
            "--status-output", ('"{0}"' -f $statusOutput),
            "--notify-failure"
        )
        $automationProcess = Start-Process `
            -FilePath $unrealPythonWindowless `
            -ArgumentList $detachedArguments `
            -WorkingDirectory $projectRoot `
            -WindowStyle Hidden `
            -PassThru
        Write-Host (
            "[后台] 就绪检查已启动（PID {0}）。UE 将保持前台全速运行。" -f
                $automationProcess.Id
        ) -ForegroundColor Green
        Write-Host "完成后 UE 会显示：DEMO READY | 100 PEOPLE"
        Write-Host "状态文件：$statusOutput"
        exit 0
    }

    & $unrealPython `
        $orchestrator `
        --expected-population $ExpectedPopulation `
        --editor-timeout $EditorTimeoutSeconds `
        --demo-timeout $DemoTimeoutSeconds `
        --status-output $statusOutput
    if ($LASTEXITCODE -ne 0) {
        throw "一键演示未通过就绪检查，退出码：$LASTEXITCODE"
    }

    $editorProcess = Get-Process -Name "UnrealEditor" -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($editorProcess) {
        try {
            $shell = New-Object -ComObject WScript.Shell
            [void]$shell.AppActivate($editorProcess.Id)
        }
        catch {
            Write-Verbose "Unable to focus Unreal Editor: $_"
        }
    }

    Write-Host ""
    Write-Host "演示已经准备好。请直接查看 Unreal Editor；结束时按 Esc。" -ForegroundColor Green
    Write-Host "状态文件：$statusOutput"
    exit 0
}
catch {
    Write-Host ""
    Write-Host "启动失败：$($_.Exception.Message)" -ForegroundColor Red
    Write-Host "状态文件：$statusOutput" -ForegroundColor Yellow
    Write-Host "不要重复启动第二个 UE；请根据上面的明确错误处理后重试。" -ForegroundColor Yellow
    exit 1
}
