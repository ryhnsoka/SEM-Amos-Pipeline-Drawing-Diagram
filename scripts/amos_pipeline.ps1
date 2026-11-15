<#
.SYNOPSIS
    Universal standard pipeline for AMOS 23 SEM diagrams:
      1) Validate spec JSON against spec/diagram_standard.json + collision gate + emit VB/HTML
      2) Compile x86 AmosGraphics IPlugin DLL
      3) Verify IPlugin contract via 32-bit reflection + stage SAV (when configured)
      4) Run headless AmosEngineLib fit verification (when SAV configured)
      5) Deploy verified DLL to Amos 23 Plugins (requires Administrator PowerShell)

.EXAMPLE
    # Build & validate directly from a compact .txt model description:
    powershell -ExecutionPolicy Bypass -File scripts\amos_pipeline.ps1 -SpecFile spec\template_model.txt

    # Build & validate the neutral JSON template:
    powershell -ExecutionPolicy Bypass -File scripts\amos_pipeline.ps1 -SpecFile spec\template_spec.json

    # Build, validate, & run headless AMOS fit for a specific project preset:
    powershell -ExecutionPolicy Bypass -File scripts\amos_pipeline.ps1 -SpecFile presets\sustainable_business.json

    # Deploy to Amos 23 Plugins (Administrator PowerShell):
    powershell -ExecutionPolicy Bypass -File scripts\amos_pipeline.ps1 -SpecFile spec\my_project.txt -Deploy
#>
[CmdletBinding()]
param(
    [string]$SpecFile = "spec\template_spec.json",
    [double]$FontSize = 0,
    [string]$SavFile = "",
    [switch]$SkipSpecGen,
    [switch]$Deploy,
    [switch]$KeepOldPlugins
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$inputPath = if ([System.IO.Path]::IsPathRooted($SpecFile)) {
    $SpecFile
} elseif (Test-Path (Join-Path (Get-Location).Path $SpecFile)) {
    Join-Path (Get-Location).Path $SpecFile
} else {
    Join-Path $projectRoot $SpecFile
}
if (-not (Test-Path $inputPath)) {
    Write-Error "Spec file not found: $inputPath"
    exit 1
}

# --- Step 0 (Optional): Convert .txt model DSL -> .json spec ---------------
if ($inputPath.ToLowerInvariant().EndsWith(".txt")) {
    $txtScript = Join-Path $projectRoot "scripts\text_to_spec.py"
    if (-not (Test-Path $txtScript)) {
        Write-Error "Text parser script not found: $txtScript"
        exit 1
    }
    $stem = [System.IO.Path]::GetFileNameWithoutExtension($inputPath)
    $specPath = Join-Path $projectRoot "plan\$stem.spec.json"
    Write-Host "[0/5] Converting text model $SpecFile -> $specPath"
    & python $txtScript --in $inputPath --out $specPath
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Text-to-spec conversion failed (exit code $LASTEXITCODE)."
        exit 1
    }
} else {
    $specPath = $inputPath
}

$specJson = Get-Content -LiteralPath $specPath -Raw -Encoding UTF8 | ConvertFrom-Json
$rootNamespace = if ($specJson.root_namespace) { [string]$specJson.root_namespace } else { "StandardSEM" }
$pluginRel = if ($specJson.plugin_source) { [string]$specJson.plugin_source } else { "scripts\GeneratedSEM.vb" }
$savRel = if (-not [string]::IsNullOrWhiteSpace($SavFile)) { $SavFile } elseif ($specJson.sav_file) { [string]$specJson.sav_file } else { "" }

$srcPath = if ([System.IO.Path]::IsPathRooted($pluginRel)) { $pluginRel } else { Join-Path $projectRoot $pluginRel }
$headlessSrcPath = Join-Path $projectRoot "scripts\HeadlessFit.vb"
$buildDir = Join-Path $projectRoot "build"
$dllPath = Join-Path $buildDir "$rootNamespace.dll"
$headlessExePath = Join-Path $buildDir "HeadlessFit.exe"

# Locate IBM SPSS Amos 23 dynamically
$programFilesX86 = ${env:ProgramFiles(x86)}
$amosCandidates = @(
    (Join-Path $programFilesX86 "IBM\SPSS\Amos\23"),
    (Join-Path $env:ProgramFiles "IBM\SPSS\Amos\23")
)
$amosDir = $amosCandidates | Where-Object { $_ -and (Test-Path (Join-Path $_ "AmosGraphics.dll")) } | Select-Object -First 1
if (-not $amosDir) {
    Write-Error "IBM SPSS Amos 23 installation directory not found."
    exit 1
}

$vbcPath = Join-Path $env:WINDIR "Microsoft.NET\Framework\v4.0.30319\vbc.exe"
$fwDir = Split-Path -Parent $vbcPath
if (-not (Test-Path $vbcPath)) {
    Write-Error "32-bit .NET Framework v4.0 vbc.exe not found at $vbcPath"
    exit 1
}

# --- Step 1: Single-source-of-truth generation + diagram_standard.json gate ---
if (-not $SkipSpecGen) {
    $genScript = Join-Path $projectRoot "scripts\generate_from_spec.py"
    if (-not (Test-Path $genScript)) {
        Write-Error "Generator script not found: $genScript"
        exit 1
    }
    Write-Host "[1/5] Generating from $SpecFile & checking STANDARDS.md..."
    $genArgs = @($genScript, "--spec", $specPath)
    if ($FontSize -gt 0) {
        $genArgs += @("--font", [string]$FontSize)
    }
    & python @genArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Standard generator / collision gate failed (exit code $LASTEXITCODE)."
        exit 1
    }
}

if (-not (Test-Path $srcPath)) {
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
    Write-Error "vbc.exe compilation failed (exit code $LASTEXITCODE)."
    exit 1
}

$buildInfo = Get-Item $dllPath
$buildHash = (Get-FileHash $dllPath -Algorithm SHA256).Hash
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
    Write-Error "x86 IPlugin verification failed: $refOut"
    exit 1
}
$refOut | Where-Object { $_ -is [string] -and $_ -match "Type=" } | ForEach-Object { Write-Host $_ }

$resolvedSav = ""
if (-not [string]::IsNullOrWhiteSpace($savRel)) {
    $resolvedSav = if ([System.IO.Path]::IsPathRooted($savRel)) { $savRel } else { Join-Path $projectRoot $savRel }
    if (-not (Test-Path $resolvedSav)) {
        Write-Error "SAV file not found: $resolvedSav"
        exit 1
    }
    $amosLocalDir = Join-Path $env:LOCALAPPDATA "AmosDevelopment\Amos\23"
    New-Item -ItemType Directory -Force -Path $amosLocalDir | Out-Null
    $localSav = Join-Path $amosLocalDir (Split-Path -Leaf $resolvedSav)
    Copy-Item -LiteralPath $resolvedSav -Destination $localSav -Force
    Write-Host "      Staged SAV to $localSav"
}

# --- Step 4: Headless statistical verification via AmosEngineLib ------------
if ($resolvedSav -and (Test-Path $headlessSrcPath)) {
    Write-Host "[4/5] Compiling & running HeadlessFit (AmosEngineLib) on SAV..."
    & $vbcPath /nologo /platform:x86 /target:exe /optionstrict+ /optionexplicit+ `
        "/out:$headlessExePath" `
        "/reference:$(Join-Path $amosDir 'AmosEngineLib.dll')" `
        "/reference:$(Join-Path $amosDir 'MiscAmosTypes.dll')" `
        $headlessSrcPath
    if ($LASTEXITCODE -ne 0) {
        Write-Error "HeadlessFit compilation failed (exit code $LASTEXITCODE)."
        exit 1
    }
    $prevAmosEnv = $env:AMOS_DIR
    $env:AMOS_DIR = $amosDir
    Push-Location $amosDir
    try {
        $fitLines = & $headlessExePath $resolvedSav
        $fitExit = $LASTEXITCODE
    } finally {
        Pop-Location
        $env:AMOS_DIR = $prevAmosEnv
    }
    $fitLines | ForEach-Object { Write-Host "      $_" }
    if ($fitExit -ne 0) {
        Write-Error "HeadlessFit model estimation failed (exit code $fitExit)."
        exit 1
    }
} else {
    Write-Host "[4/5] Skipped HeadlessFit (no sav_file in spec and no -SavFile supplied)."
}

# --- Step 5: Deploy to Amos Plugins (requires Administrator) ----------------
if ($Deploy) {
    Write-Host "[5/5] Deploying DLL to Amos Plugins..."
    if (Get-Process -Name "AmosGraphicsCLI" -ErrorAction SilentlyContinue) {
        Write-Error "AmosGraphicsCLI is currently running. Close Amos first so the DLL is not locked."
        exit 1
    }
    $pluginsDir = Join-Path $amosDir "Plugins"
    $targetLeaf = Split-Path -Leaf $dllPath
    $pluginDest = Join-Path $pluginsDir $targetLeaf
    Copy-Item -LiteralPath $dllPath -Destination $pluginDest -Force
    $destInfo = Get-Item $pluginDest
    $destHash = (Get-FileHash $pluginDest -Algorithm SHA256).Hash
    if ($destHash -ne $buildHash -or $destInfo.Length -ne $buildInfo.Length) {
        Write-Error "Deployment verification failed: hash or size mismatch!"
        exit 1
    }
    Write-Host ("      Deployed & verified: {0} ({1} bytes | {2})" -f $pluginDest, $destInfo.Length, $destHash)

    if (-not $KeepOldPlugins) {
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
                Remove-Item -LiteralPath $_.FullName -Force
                Write-Host ("      Removed old custom plugin: {0}" -f $_.Name)
            }
        }
    }
} else {
    Write-Host "[5/5] All standard checks passed. To deploy to Amos Plugins, run in Administrator PowerShell:"
    Write-Host ("      amos-sem -SpecFile `"{0}`" -Deploy" -f $SpecFile)
}

exit 0
