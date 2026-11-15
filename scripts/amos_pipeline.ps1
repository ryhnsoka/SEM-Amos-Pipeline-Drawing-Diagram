<#
.SYNOPSIS
    Universal standard pipeline for AMOS 23 SEM diagrams:
      0) Convert .txt model DSL -> .spec.json (when input is .txt)
      1) Validate spec & .sav dataset, run 0-collision gate, emit VB/HTML & dataset report
      2) Compile x86 AmosGraphics IPlugin DLL
      3) Verify IPlugin contract via 32-bit reflection + stage SAV with SHA-256 suffix
      4) Run headless AmosEngineLib fit verification (when SAV configured and -SkipFit not set)
      5) Deploy verified DLL non-destructively to Amos 23 Plugins (supports -WhatIf, -PruneOldPlugins)

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\amos_pipeline.ps1 -SpecFile spec\template_model.txt

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\amos_pipeline.ps1 -SpecFile presets\green_transparency_advocacy.txt -SavFile "data\research.sav" -ValidateOnly

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\amos_pipeline.ps1 -SpecFile spec\my_project.txt -SavFile "data\research.sav" -Deploy -WhatIf
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$SpecFile = "spec\template_spec.json",
    [double]$FontSize = 0,
    [string]$SavFile = "",
    [switch]$SkipSpecGen,
    [switch]$ValidateOnly,
    [switch]$SkipFit,
    [switch]$Deploy,
    [switch]$PruneOldPlugins,
    [switch]$KeepOldPlugins,
    [string]$PluginsDirOverride = ""
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$planDir = Join-Path $projectRoot "plan"
$buildDir = Join-Path $projectRoot "build"
New-Item -ItemType Directory -Force -Path $planDir | Out-Null

$executionReportPath = Join-Path $planDir "execution_report.json"
$datasetReportPath = Join-Path $planDir "dataset_report.json"

$report = [ordered]@{
    timestamp = (Get-Date).ToString("o")
    spec_file = $SpecFile
    statuses = [ordered]@{
        specification_validation = "pending"
        dataset_validation = "pending"
        diagram_generation = "pending"
        geometric_validation = "pending"
        dll_compilation = "pending"
        plugin_contract_verification = "pending"
        dataset_binding = "not_configured"
        statistical_estimation = "pending"
    }
    estimation_details = [ordered]@{
        fit_status = $null
        admissible = $null
        cmin = $null
        df = $null
        rmsea = $null
        text_output_file = $null
        error = $null
    }
    artifacts = [ordered]@{
        spec_path = $null
        dataset_report = $datasetReportPath
        layout_json = (Join-Path $planDir "layout.json")
        assessment_html = (Join-Path $planDir "assessment.html")
        vb_source = $null
        dll_path = $null
        dll_sha256 = $null
        staged_sav_path = $null
        deployed_dll = $null
    }
}

function Write-ExecutionReport {
    $jsonText = $report | ConvertTo-Json -Depth 6
    [System.IO.File]::WriteAllText($executionReportPath, $jsonText, (New-Object System.Text.UTF8Encoding($false)))
}

$inputPath = if ([System.IO.Path]::IsPathRooted($SpecFile)) {
    $SpecFile
} elseif (Test-Path (Join-Path (Get-Location).Path $SpecFile)) {
    Join-Path (Get-Location).Path $SpecFile
} else {
    Join-Path $projectRoot $SpecFile
}
if (-not (Test-Path $inputPath)) {
    $report.statuses.specification_validation = "failed"
    Write-ExecutionReport
    Write-Error "Spec file not found: $inputPath"
    exit 1
}

# --- Step 0 (Optional): Convert .txt model DSL -> .json spec ---------------
if ($inputPath.ToLowerInvariant().EndsWith(".txt")) {
    $txtScript = Join-Path $projectRoot "scripts\text_to_spec.py"
    if (-not (Test-Path $txtScript)) {
        $report.statuses.specification_validation = "failed"
        Write-ExecutionReport
        Write-Error "Text parser script not found: $txtScript"
        exit 1
    }
    $stem = [System.IO.Path]::GetFileNameWithoutExtension($inputPath)
    $specPath = Join-Path $planDir "$stem.spec.json"
    Write-Host "[0/5] Converting text model $SpecFile -> $specPath"
    & python $txtScript --in $inputPath --out $specPath
    if ($LASTEXITCODE -ne 0) {
        $report.statuses.specification_validation = "failed"
        Write-ExecutionReport
        Write-Error "Text-to-spec conversion failed (exit code $LASTEXITCODE)."
        exit 1
    }
} else {
    $specPath = $inputPath
}
$report.artifacts.spec_path = $specPath

$specJson = Get-Content -LiteralPath $specPath -Raw -Encoding UTF8 | ConvertFrom-Json
$rootNamespace = if ($specJson.root_namespace) { [string]$specJson.root_namespace } else { "StandardSEM" }
$pluginRel = if ($specJson.plugin_source) { [string]$specJson.plugin_source } else { "scripts\GeneratedSEM.vb" }

# --- Unified SAV resolution: 1) -SavFile, 2) spec.sav_file, 3) none ------
$resolvedSav = ""
if (-not [string]::IsNullOrWhiteSpace($SavFile)) {
    $savCandidate = $SavFile.Trim()
    if ([System.IO.Path]::IsPathRooted($savCandidate)) {
        $resolvedSav = $savCandidate
    } elseif (Test-Path (Join-Path (Get-Location).Path $savCandidate)) {
        $resolvedSav = (Resolve-Path (Join-Path (Get-Location).Path $savCandidate)).Path
    } elseif (Test-Path (Join-Path $projectRoot $savCandidate)) {
        $resolvedSav = (Resolve-Path (Join-Path $projectRoot $savCandidate)).Path
    } else {
        $resolvedSav = Join-Path (Get-Location).Path $savCandidate
    }
} elseif ($specJson.sav_file -and -not [string]::IsNullOrWhiteSpace([string]$specJson.sav_file)) {
    $savCandidate = ([string]$specJson.sav_file).Trim()
    $specDir = Split-Path -Parent $specPath
    if ([System.IO.Path]::IsPathRooted($savCandidate)) {
        $resolvedSav = $savCandidate
    } elseif (Test-Path (Join-Path $projectRoot $savCandidate)) {
        $resolvedSav = (Resolve-Path (Join-Path $projectRoot $savCandidate)).Path
    } elseif (Test-Path (Join-Path $specDir $savCandidate)) {
        $resolvedSav = (Resolve-Path (Join-Path $specDir $savCandidate)).Path
    } else {
        $resolvedSav = Join-Path $projectRoot $savCandidate
    }
}

$srcPath = if ([System.IO.Path]::IsPathRooted($pluginRel)) { $pluginRel } else { Join-Path $projectRoot $pluginRel }
$headlessSrcPath = Join-Path $projectRoot "scripts\HeadlessFit.vb"
$dllPath = Join-Path $buildDir "$rootNamespace.dll"
$headlessExePath = Join-Path $buildDir "HeadlessFit.exe"
$report.artifacts.vb_source = $srcPath
$report.artifacts.dll_path = $dllPath

# --- Step 1: Single-source-of-truth validation, dataset preflight & generation
if (-not $SkipSpecGen) {
    $genScript = Join-Path $projectRoot "scripts\generate_from_spec.py"
    if (-not (Test-Path $genScript)) {
        $report.statuses.specification_validation = "failed"
        Write-ExecutionReport
        Write-Error "Generator script not found: $genScript"
        exit 1
    }
    Write-Host "[1/5] Validating spec/dataset & generating diagram from $SpecFile..."
    $genArgs = @($genScript, "--spec", $specPath, "--dataset-report", $datasetReportPath)
    if ($FontSize -gt 0) {
        $genArgs += @("--font", [string]$FontSize)
    }
    if (-not [string]::IsNullOrWhiteSpace($resolvedSav)) {
        $genArgs += @("--sav", $resolvedSav)
    }
    if ($ValidateOnly) {
        $genArgs += "--validate-only"
    }
    & python @genArgs
    $genExit = $LASTEXITCODE

    if (Test-Path $datasetReportPath) {
        $dsObj = Get-Content -LiteralPath $datasetReportPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $report.statuses.dataset_validation = [string]$dsObj.validation_status
        $report.statuses.dataset_binding = [string]$dsObj.binding_status
    }

    if ($genExit -ne 0) {
        if ($report.statuses.dataset_validation -eq "failed") {
            $report.statuses.specification_validation = "passed"
            $report.statuses.diagram_generation = "skipped"
            $report.statuses.geometric_validation = "skipped"
        } elseif (Test-Path (Join-Path $planDir "layout.json")) {
            $report.statuses.specification_validation = "passed"
            $report.statuses.diagram_generation = "passed"
            $report.statuses.geometric_validation = "failed"
        } else {
            $report.statuses.specification_validation = "failed"
        }
        Write-ExecutionReport
        Write-Error "Standard generator / validation gate failed (exit code $genExit)."
        exit 1
    }
    $report.statuses.specification_validation = "passed"
    $report.statuses.diagram_generation = "passed"
    $report.statuses.geometric_validation = "passed"
} else {
    $report.statuses.specification_validation = "skipped"
    $report.statuses.dataset_validation = "skipped"
    $report.statuses.diagram_generation = "skipped"
    $report.statuses.geometric_validation = "skipped"
}

if ($ValidateOnly) {
    $report.statuses.dll_compilation = "skipped"
    $report.statuses.plugin_contract_verification = "skipped"
    $report.statuses.statistical_estimation = "skipped"
    Write-ExecutionReport
    Write-Host "[VALIDATE-ONLY] Spec, dataset, and geometry checks passed. Report: $executionReportPath"
    exit 0
}

# Locate IBM SPSS Amos 23 dynamically
$programFilesX86 = ${env:ProgramFiles(x86)}
$amosCandidates = @(
    (Join-Path $programFilesX86 "IBM\SPSS\Amos\23"),
    (Join-Path $env:ProgramFiles "IBM\SPSS\Amos\23")
)
$amosDir = $amosCandidates | Where-Object { $_ -and (Test-Path (Join-Path $_ "AmosGraphics.dll")) } | Select-Object -First 1
if (-not $amosDir) {
    $report.statuses.dll_compilation = "failed"
    Write-ExecutionReport
    Write-Error "IBM SPSS Amos 23 installation directory not found."
    exit 2
}

$vbcPath = Join-Path $env:WINDIR "Microsoft.NET\Framework\v4.0.30319\vbc.exe"
$fwDir = Split-Path -Parent $vbcPath
if (-not (Test-Path $vbcPath)) {
    $report.statuses.dll_compilation = "failed"
    Write-ExecutionReport
    Write-Error "32-bit .NET Framework v4.0 vbc.exe not found at $vbcPath"
    exit 2
}

if (-not (Test-Path $srcPath)) {
    $report.statuses.dll_compilation = "failed"
    Write-ExecutionReport
    Write-Error "Plugin source not found: $srcPath"
    exit 1
}

# --- Step 2: Compile x86 plugin DLL -----------------------------------------
New-Item -ItemType Directory -Force -Path $buildDir | Out-Null
Write-Host "[2/5] Compiling x86 plugin: $srcPath -> $dllPath"
& $vbcPath /nologo /target:library /platform:x86 /optionstrict+ /optionexplicit+ `
    "/rootnamespace:$rootNamespace" "/out:$dllPath" `
    "/reference:$(Join-Path $amosDir 'AmosGraphics.dll')" `
    "/reference:$(Join-Path $amosDir 'MiscAmosTypes.dll')" `
    "/reference:$(Join-Path $fwDir 'Microsoft.VisualBasic.dll')" `
    "/reference:$(Join-Path $fwDir 'System.dll')" $srcPath
if ($LASTEXITCODE -ne 0) {
    $report.statuses.dll_compilation = "failed"
    Write-ExecutionReport
    Write-Error "vbc.exe compilation failed (exit code $LASTEXITCODE)."
    exit 3
}

$buildInfo = Get-Item $dllPath
$buildHash = (Get-FileHash $dllPath -Algorithm SHA256).Hash
$report.statuses.dll_compilation = "passed"
$report.artifacts.dll_sha256 = $buildHash
Write-Host ("      Built: {0} bytes | SHA256 {1}" -f $buildInfo.Length, $buildHash)

# --- Step 3: Verify IPlugin contract in 32-bit PowerShell + stage SAV -------
Write-Host "[3/5] Verifying IPlugin metadata via 32-bit reflection..."
$ps32 = Join-Path $env:WINDIR "SysWOW64\WindowsPowerShell\v1.0\powershell.exe"
$refOut = & $ps32 -NoProfile -Command {
    param($amosPath, $targetDll)
    [Reflection.Assembly]::LoadFrom((Join-Path $amosPath "AmosGraphics.dll")) | Out-Null
    [Reflection.Assembly]::LoadFrom((Join-Path $amosPath "MiscAmosTypes.dll")) | Out-Null
    $asm = [Reflection.Assembly]::LoadFrom($targetDll)
    $pluginType = $asm.GetTypes() | Where-Object { $_.GetInterface("AmosGraphics.IPlugin") -ne $null } | Select-Object -First 1
    if (-not $pluginType -or -not $pluginType.IsPublic) {
        throw "No public AmosGraphics.IPlugin implementation found in $targetDll"
    }
    $obj = [Activator]::CreateInstance($pluginType)
    Write-Output ("      Type={0}; Name=[{1}]" -f $pluginType.FullName, $obj.Name())
} -Args $amosDir, $dllPath 2>&1
if ($LASTEXITCODE -ne 0) {
    $report.statuses.plugin_contract_verification = "failed"
    Write-ExecutionReport
    Write-Error "x86 IPlugin verification failed: $refOut"
    exit 4
}
$report.statuses.plugin_contract_verification = "passed"
$refOut | Where-Object { $_ -is [string] -and $_ -match "Type=" } | ForEach-Object { Write-Host $_ }

if (-not [string]::IsNullOrWhiteSpace($resolvedSav)) {
    if (-not (Test-Path $resolvedSav)) {
        $report.statuses.dataset_binding = "failed"
        Write-ExecutionReport
        Write-Error "SAV file not found: $resolvedSav"
        exit 1
    }
    $stagedLeaf = ""
    if (Test-Path $datasetReportPath) {
        $dsObj = Get-Content -LiteralPath $datasetReportPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $stagedLeaf = [string]$dsObj.staged_filename
    }
    if ([string]::IsNullOrWhiteSpace($stagedLeaf)) {
        $sha8 = (Get-FileHash -LiteralPath $resolvedSav -Algorithm SHA256).Hash.Substring(0, 8).ToLowerInvariant()
        $stem = [System.IO.Path]::GetFileNameWithoutExtension($resolvedSav)
        $ext = [System.IO.Path]::GetExtension($resolvedSav)
        $stagedLeaf = "${stem}_${sha8}${ext}"
    }
    $amosLocalDir = Join-Path $env:LOCALAPPDATA "AmosDevelopment\Amos\23"
    New-Item -ItemType Directory -Force -Path $amosLocalDir | Out-Null
    $localSav = Join-Path $amosLocalDir $stagedLeaf
    Copy-Item -LiteralPath $resolvedSav -Destination $localSav -Force
    $report.statuses.dataset_binding = "staged"
    $report.artifacts.staged_sav_path = $localSav
    Write-Host "      Staged SAV (read-only copy) to $localSav"
} else {
    $report.statuses.dataset_binding = "not_configured"
}

# --- Step 4: Headless statistical verification via AmosEngineLib ------------
# Build/diagram status is kept separate from statistical estimation status.
if ($SkipFit) {
    $report.statuses.statistical_estimation = "skipped"
    Write-Host "[4/5] Skipped HeadlessFit (-SkipFit specified)."
} elseif ($resolvedSav -and (Test-Path $headlessSrcPath)) {
    Write-Host "[4/5] Compiling & running HeadlessFit (AmosEngineLib) on SAV..."
    & $vbcPath /nologo /platform:x86 /target:exe /optionstrict+ /optionexplicit+ `
        "/out:$headlessExePath" `
        "/reference:$(Join-Path $amosDir 'AmosEngineLib.dll')" `
        "/reference:$(Join-Path $amosDir 'MiscAmosTypes.dll')" `
        $headlessSrcPath
    if ($LASTEXITCODE -ne 0) {
        $report.statuses.statistical_estimation = "runtime_error"
        $report.estimation_details.error = "HeadlessFit compilation failed (exit code $LASTEXITCODE)."
        Write-Warning $report.estimation_details.error
    } else {
        $prevAmosEnv = $env:AMOS_DIR
        $env:AMOS_DIR = $amosDir
        Push-Location $amosDir
        try {
            $fitLines = & $headlessExePath $resolvedSav 2>&1
            $fitExit = $LASTEXITCODE
        } finally {
            Pop-Location
            $env:AMOS_DIR = $prevAmosEnv
        }
        $fitText = ($fitLines | ForEach-Object { [string]$_ })
        $fitText | ForEach-Object { Write-Host "      $_" }

        $joinedFit = $fitText -join "`n"
        if ($joinedFit -match "FitStatus=([-\d]+);\s*Admissible=(True|False);\s*CMIN=([^;]+);\s*DF=([^;]+);\s*RMSEA=([^\r\n]+)") {
            $report.estimation_details.fit_status = [int]$matches[1]
            $report.estimation_details.admissible = ($matches[2] -eq "True")
            $report.estimation_details.cmin = $matches[3].Trim()
            $report.estimation_details.df = $matches[4].Trim()
            $report.estimation_details.rmsea = $matches[5].Trim()
        }
        if ($joinedFit -match "TEXT_OUT=([^\r\n]+)") {
            $report.estimation_details.text_output_file = $matches[1].Trim()
        }
        if ($joinedFit -match "RUNTIME_ERROR=([^\r\n]+)") {
            $report.estimation_details.error = $matches[1].Trim()
        }

        if ($fitExit -eq 0) {
            $report.statuses.statistical_estimation = "succeeded"
        } elseif ($fitExit -eq 4 -or ($report.estimation_details.fit_status -eq 0 -and $report.estimation_details.admissible -eq $false)) {
            $report.statuses.statistical_estimation = "inadmissible"
            Write-Warning "HeadlessFit converged with inadmissible solution (diagram & DLL remain valid)."
        } elseif ($fitExit -eq 2) {
            $report.statuses.statistical_estimation = "estimation_failed"
            Write-Warning "HeadlessFit estimation failed / did not converge (FitStatus=$($report.estimation_details.fit_status)); diagram & DLL remain valid."
        } else {
            $report.statuses.statistical_estimation = "runtime_error"
            Write-Warning "HeadlessFit encountered a runtime/API error (exit code $fitExit); diagram & DLL remain valid."
        }
    }
} else {
    $report.statuses.statistical_estimation = "skipped"
    Write-Host "[4/5] Skipped HeadlessFit (no sav_file in spec and no -SavFile supplied)."
}

# --- Step 5: Non-destructive deploy to Amos Plugins -------------------------
if ($Deploy) {
    Write-Host "[5/5] Deploying DLL to Amos Plugins (non-destructive)..."
    if (-not $PluginsDirOverride -and (Get-Process -Name "AmosGraphicsCLI" -ErrorAction SilentlyContinue)) {
        Write-ExecutionReport
        Write-Error "AmosGraphicsCLI is currently running. Close Amos first so the DLL is not locked."
        exit 5
    }
    $pluginsDir = if ($PluginsDirOverride) { $PluginsDirOverride } else { Join-Path $amosDir "Plugins" }
    New-Item -ItemType Directory -Force -Path $pluginsDir | Out-Null
    $targetLeaf = Split-Path -Leaf $dllPath
    $pluginDest = Join-Path $pluginsDir $targetLeaf

    if (Test-Path $pluginDest) {
        $backupDir = Join-Path $buildDir "backups"
        $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
        $backupPath = Join-Path $backupDir "$targetLeaf.$stamp.bak"
        if ($PSCmdlet.ShouldProcess($pluginDest, "Backup existing plugin to $backupPath")) {
            New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
            Copy-Item -LiteralPath $pluginDest -Destination $backupPath -Force
            Write-Host "      Backed up existing target DLL to $backupPath"
        }
    }

    if ($PSCmdlet.ShouldProcess($pluginDest, "Copy $dllPath (SHA256 $buildHash)")) {
        Copy-Item -LiteralPath $dllPath -Destination $pluginDest -Force
        $destInfo = Get-Item $pluginDest
        $destHash = (Get-FileHash $pluginDest -Algorithm SHA256).Hash
        if ($destHash -ne $buildHash -or $destInfo.Length -ne $buildInfo.Length) {
            Write-ExecutionReport
            Write-Error "Deployment verification failed: hash or size mismatch!"
            exit 5
        }
        $report.artifacts.deployed_dll = $pluginDest
        Write-Host ("      Deployed & verified: {0} ({1} bytes | {2})" -f $pluginDest, $destInfo.Length, $destHash)
    }

    if ($PruneOldPlugins -and -not $KeepOldPlugins) {
        $builtinPlugins = @(
            "DrawCovariances.dll",
            "GrowthCurveModel.dll",
            "NameParameters.dll",
            "NameUnobservedVariables.dll",
            "ResizeObservedVariables.dll",
            "StandardizedRMR.dll"
        )
        Get-ChildItem -LiteralPath $pluginsDir -Filter "*.dll" -File | ForEach-Object {
            if (($builtinPlugins -notcontains $_.Name) -and ($_.Name -ine $targetLeaf)) {
                if ($PSCmdlet.ShouldProcess($_.FullName, "Prune old custom plugin")) {
                    Remove-Item -LiteralPath $_.FullName -Force
                    Write-Host ("      Pruned old custom plugin: {0}" -f $_.Name)
                }
            }
        }
    } else {
        Write-Host "      Preserved all other plugins in $pluginsDir (pass -PruneOldPlugins to clean up old custom plugins)."
    }
} else {
    Write-Host "[5/5] Build & checks complete. To deploy to Amos Plugins, run in Administrator PowerShell:"
    Write-Host ("      amos-sem -SpecFile `"{0}`" -Deploy" -f $SpecFile)
}

Write-ExecutionReport
exit 0
