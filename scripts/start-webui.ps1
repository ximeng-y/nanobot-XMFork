[CmdletBinding()]
param(
    [switch]$SetupOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Fail {
    param([string]$Message)
    throw $Message
}

function Test-Python311 {
    param(
        [string]$Executable,
        [string[]]$PrefixArgs = @()
    )

    try {
        & $Executable @PrefixArgs -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" *> $null
        return $LASTEXITCODE -eq 0
    }
    catch {
        return $false
    }
}

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$DataRoot = Join-Path $ProjectRoot "userdatas"
$VenvDir = Join-Path $DataRoot "venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$NanobotHome = Join-Path $DataRoot "nanobot"
$HomeDir = Join-Path $DataRoot "home"
$TempDir = Join-Path $DataRoot "temp"
$PipCacheDir = Join-Path $DataRoot "cache\pip"
$BunCacheDir = Join-Path $DataRoot "cache\bun"
$NpmCacheDir = Join-Path $DataRoot "cache\npm"
$UvCacheDir = Join-Path $DataRoot "cache\uv"
$PythonCacheDir = Join-Path $DataRoot "cache\python"
$RoamingAppData = Join-Path $DataRoot "appdata\roaming"
$LocalAppData = Join-Path $DataRoot "appdata\local"
$InstallStamp = Join-Path $DataRoot "install.stamp"
$Pyproject = Join-Path $ProjectRoot "pyproject.toml"

$Directories = @(
    $DataRoot,
    $NanobotHome,
    $HomeDir,
    $TempDir,
    $PipCacheDir,
    $BunCacheDir,
    $NpmCacheDir,
    $UvCacheDir,
    $PythonCacheDir,
    $RoamingAppData,
    $LocalAppData
)

foreach ($Directory in $Directories) {
    New-Item -ItemType Directory -Force -Path $Directory | Out-Null
}

# Keep subprocess caches and user-level state inside the checkout.
$env:HOME = $HomeDir
$env:USERPROFILE = $HomeDir
$env:APPDATA = $RoamingAppData
$env:LOCALAPPDATA = $LocalAppData
$env:TEMP = $TempDir
$env:TMP = $TempDir
$env:PIP_CACHE_DIR = $PipCacheDir
$env:PIP_DISABLE_PIP_VERSION_CHECK = "1"
$env:BUN_INSTALL = Join-Path $DataRoot "tools\bun"
$env:BUN_INSTALL_CACHE_DIR = $BunCacheDir
$env:npm_config_cache = $NpmCacheDir
$env:NPM_CONFIG_CACHE = $NpmCacheDir
$env:UV_CACHE_DIR = $UvCacheDir
$env:PYTHONPYCACHEPREFIX = $PythonCacheDir
$env:PYTHONNOUSERSITE = "1"
$env:PYTHONUTF8 = "1"

Write-Host "Data root: $DataRoot"
Write-Host "Config:    $(Join-Path $NanobotHome 'config.json')"
Write-Host "Workspace: $(Join-Path $NanobotHome 'workspace')"

if (-not (Test-Path -LiteralPath $VenvPython)) {
    $BasePython = $env:NANOBOT_PYTHON
    $BasePythonArgs = @()

    if (-not $BasePython) {
        $PythonCommand = Get-Command python -ErrorAction SilentlyContinue
        if ($null -ne $PythonCommand) {
            $BasePython = $PythonCommand.Source
        }
    }

    if (-not $BasePython) {
        $PythonLauncher = Get-Command py -ErrorAction SilentlyContinue
        if ($null -ne $PythonLauncher) {
            $BasePython = $PythonLauncher.Source
            $BasePythonArgs = @("-3")
        }
    }

    if (-not $BasePython -or -not (Test-Python311 $BasePython $BasePythonArgs)) {
        Fail "Python 3.11 or newer was not found. Install Python 3.11+ and try again."
    }

    Write-Host "Creating local Python environment..."
    & $BasePython @BasePythonArgs -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) {
        Fail "Could not create the local Python environment."
    }
}

if (-not (Test-Python311 $VenvPython)) {
    Fail "The local Python environment is broken. Remove '$VenvDir' and try again."
}

$NeedsInstall = $true
if (Test-Path -LiteralPath $InstallStamp) {
    $StampTime = (Get-Item -LiteralPath $InstallStamp).LastWriteTimeUtc
    $ProjectTime = (Get-Item -LiteralPath $Pyproject).LastWriteTimeUtc
    if ($StampTime -ge $ProjectTime) {
        & $VenvPython -c "import importlib.metadata as m; m.version('nanobot-ai')" *> $null
        if ($LASTEXITCODE -eq 0) {
            $NeedsInstall = $false
        }
    }
}

if ($NeedsInstall) {
    Write-Host "Installing or refreshing source dependencies..."
    & $VenvPython -m pip install --no-input --disable-pip-version-check -e $ProjectRoot
    if ($LASTEXITCODE -ne 0) {
        Fail "Could not install source dependencies."
    }

    $UtcNow = [DateTime]::UtcNow.ToString("o")
    [System.IO.File]::WriteAllText($InstallStamp, $UtcNow)
}

# Hide any Bun already installed on the system, then use the checkout-local runtime.
$PathEntries = @()
foreach ($Entry in ($env:PATH -split [System.IO.Path]::PathSeparator)) {
    if (-not $Entry) {
        continue
    }

    $HasBun = (Test-Path -LiteralPath (Join-Path $Entry "bun.exe")) -or
        (Test-Path -LiteralPath (Join-Path $Entry "bun.cmd")) -or
        (Test-Path -LiteralPath (Join-Path $Entry "bun.bat"))
    if (-not $HasBun) {
        $PathEntries += $Entry
    }
}
$env:PATH = $PathEntries -join [System.IO.Path]::PathSeparator

$LocalBunPath = & $VenvPython -c "from nanobot.bun import ensure_bun; print(ensure_bun(output=lambda _: None))"
if ($LASTEXITCODE -ne 0 -or -not $LocalBunPath) {
    Fail "Could not prepare the local Bun runtime."
}

$LocalBunDir = Split-Path -Parent ([string]$LocalBunPath).Trim()
$env:PATH = $LocalBunDir + [System.IO.Path]::PathSeparator + $env:PATH

if ($SetupOnly) {
    & $VenvPython -m nanobot --version
    if ($LASTEXITCODE -ne 0) {
        Fail "The local nanobot installation could not be started."
    }
    Write-Host "Local setup is ready."
    exit 0
}

$NanobotArgs = @("-m", "nanobot", "--home", $NanobotHome, "webui")
if (-not (Test-Path -LiteralPath (Join-Path $NanobotHome "config.json"))) {
    $NanobotArgs += "--yes"
}

& $VenvPython @NanobotArgs
$ExitCode = $LASTEXITCODE
if ($ExitCode -ne 0) {
    Fail "WebUI exited with code $ExitCode."
}
