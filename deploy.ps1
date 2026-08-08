#requires -Version 5.1

param(
    [string]$ConfigFile
)

$SCRIPT_DIR = $PSScriptRoot
$FIREFLY_REPO = "https://github.com/qiyueling2716/Firefly-Blog"
$GITHUB_MIRRORS = @(
    "https://ghfast.top/https://github.com",
    "https://gh-proxy.com/https://github.com",
    "https://ghproxy.net/https://github.com"
)

$DeployMode = if ($env:DEPLOY_MODE) { $env:DEPLOY_MODE } else { "local_only" }
$LocalBlogRoot = if ($env:LOCAL_BLOG_ROOT) { $env:LOCAL_BLOG_ROOT } else { "D:\www\firefly" }
$WebRoot = if ($env:WEB_ROOT) { $env:WEB_ROOT } else { "D:\www\firefly" }

# 网络加速选项：USE_CN_MIRROR=1 启用国内镜像（克隆候选 + npm/pnpm 源）
$UseCnMirror = if ($env:USE_CN_MIRROR) { $env:USE_CN_MIRROR -eq "1" } else { $false }
$NpmRegistry = if ($env:NPM_REGISTRY) { $env:NPM_REGISTRY } else { "" }
$CloneDepth = if ($env:CLONE_DEPTH) { [int]$env:CLONE_DEPTH } else { 1 }
$GitCloneTimeout = if ($env:GIT_CLONE_TIMEOUT) { [int]$env:GIT_CLONE_TIMEOUT } else { 120 }
if ($UseCnMirror -and -not $NpmRegistry) { $NpmRegistry = "https://registry.npmmirror.com" }

function Get-NpmRegistryArg {
    if ($NpmRegistry) {
        return "--registry=$NpmRegistry"
    }
    return $null
}

function Write-Info {
    Write-Host "[INFO] $args"
}

function Write-Ok {
    Write-Host "[OK] $args" -ForegroundColor Green
}

function Write-Warn {
    Write-Host "[WARN] $args" -ForegroundColor Yellow
}

function Write-Err {
    Write-Host "[ERROR] $args" -ForegroundColor Red
}

function Test-CommandExists {
    param([string]$Command)
    $null = Get-Command $Command -ErrorAction SilentlyContinue
    return $?
}

function Get-CommandPath {
    param([string]$Command)
    $cmd = Get-Command $Command -ErrorAction SilentlyContinue
    if ($cmd) {
        return $cmd.Source
    }
    return $null
}

function Check-NodeJs {
    Write-Info "Checking Node.js environment..."

    if (-not (Test-CommandExists "node")) {
        Write-Warn "Node.js not found, installing..."
        
        if (Test-CommandExists "winget") {
            Write-Info "Installing Node.js via winget..."
            $process = Start-Process -FilePath "winget" -ArgumentList "install --id OpenJS.NodeJS.LTS --silent --accept-package-agreements --accept-source-agreements" -Wait -PassThru -NoNewWindow
            if ($process.ExitCode -eq 0 -or $process.ExitCode -eq 1618) {
                Write-Ok "Node.js installed"
            }
        }
    }

    if (-not (Test-CommandExists "node")) {
        Write-Err "Node.js not found, please install manually"
        Write-Info "Download: https://nodejs.org/"
        exit 1
    }

    $nodeVersion = node --version
    Write-Ok "Node.js $nodeVersion"
}

function Check-Pnpm {
    Write-Info "Checking pnpm..."

    if (-not (Test-CommandExists "pnpm")) {
        Write-Warn "pnpm not found, installing via npm..."
        if (Test-CommandExists "npm") {
            Write-Info "Installing pnpm...$(if ($NpmRegistry) { " (registry: $NpmRegistry)" })"
            $regArg = Get-NpmRegistryArg
            if ($regArg) {
                npm install -g pnpm $regArg
            } else {
                npm install -g pnpm
            }
            if ($LASTEXITCODE -eq 0) {
                Write-Ok "pnpm installed"
            }
        }
    }

    if (-not (Test-CommandExists "pnpm")) {
        Write-Err "pnpm not found, please install manually"
        exit 1
    }

    $pnpmPath = Get-CommandPath "pnpm"
    $script:PNPM_CMD = if ($pnpmPath) { $pnpmPath } else { "pnpm" }
    $pnpmVersion = pnpm --version
    Write-Ok "pnpm $pnpmVersion"
}

function Clone-FireflyRepo {
    param([string]$BlogRoot)

    # 候选源：显式镜像 → 内置镜像（自动 fallback）→ 官方
    $candidates = @()
    if ($env:GITHUB_MIRROR) {
        $candidates += "$($env:GITHUB_MIRROR)/qiyueling2716/Firefly-Blog.git"
    }
    if ($UseCnMirror -or -not $env:GITHUB_MIRROR) {
        foreach ($m in $GITHUB_MIRRORS) {
            $candidates += "$m/qiyueling2716/Firefly-Blog.git"
        }
    }
    $candidates += $FIREFLY_REPO

    $depthArgs = @()
    if ($CloneDepth -gt 0) {
        $depthArgs = @("--depth", "$CloneDepth", "--single-branch")
    }

    foreach ($cand in $candidates) {
        Write-Info "Cloning (timeout ${GitCloneTimeout}s): $cand"
        $scriptBlock = {
            param($Cand, $Target, $DepthArgs)
            git clone @DepthArgs $Cand $Target 2>&1 | ForEach-Object { Write-Output $_ }
            Write-Output "GIT_EXIT_CODE=$LASTEXITCODE"
        }
        $job = Start-Job -ScriptBlock $scriptBlock -ArgumentList $cand, $BlogRoot, $depthArgs
        if (-not (Wait-Job -Job $job -Timeout $GitCloneTimeout)) {
            Stop-Job -Job $job -Force
            Remove-Job -Job $job -Force
            Write-Warn "Clone timed out after ${GitCloneTimeout}s, trying next source..."
            if (Test-Path $BlogRoot) { Remove-Item $BlogRoot -Recurse -Force -ErrorAction SilentlyContinue }
            continue
        }
        $jobOut = Receive-Job -Job $job -Keep
        Remove-Job -Job $job -Force
        $exitLine = $jobOut | Where-Object { $_ -match "^GIT_EXIT_CODE=(\d+)$" }
        $exitCode = if ($exitLine) { [int]($exitLine -replace "GIT_EXIT_CODE=", "") } else { 1 }
        if ($exitCode -eq 0) {
            Write-Ok "Cloned successfully: $cand"
            return $true
        }
        Write-Warn "Clone failed, trying next source..."
        if (Test-Path $BlogRoot) { Remove-Item $BlogRoot -Recurse -Force -ErrorAction SilentlyContinue }
    }

    Write-Err "All clone sources failed (network / mirror down / timeout)"
    Write-Info "Try: git clone $FIREFLY_REPO $BlogRoot"
    return $false
}

function Check-FireflyProject {
    param([string]$BlogRoot)

    Write-Info "Checking Firefly project..."

    if (-not (Test-Path $BlogRoot)) {
        Write-Info "Creating directory: $BlogRoot"
        New-Item -ItemType Directory -Path $BlogRoot -Force | Out-Null
    }

    $packageJson = Join-Path $BlogRoot "package.json"
    if (-not (Test-Path $packageJson)) {
        Write-Warn "Empty directory, cloning Firefly repository..."

        if (Test-CommandExists "git") {
            if (-not (Clone-FireflyRepo $BlogRoot)) {
                exit 1
            }
        } else {
            Write-Err "git not found, please install Git"
            exit 1
        }
    }

    Write-Ok "Firefly project verified: $BlogRoot"
}

function Install-BlogDeps {
    param([string]$BlogRoot)

    Write-Info "Installing blog dependencies..."

    $nodeModules = Join-Path $BlogRoot "node_modules"
    if (Test-Path $nodeModules) {
        Write-Info "node_modules already exists, skipping"
        return
    }

    # 写入 .npmrc（镜像源 + 重试/超时策略，防止 pnpm 静默卡住）
    $npmrc = @(
        "fetch-retries=3",
        "fetch-retry-mintimeout=2000",
        "fetch-retry-maxtimeout=60000",
        "fetch-timeout=120000"
    )
    if ($NpmRegistry) {
        $npmrc += "registry=$NpmRegistry"
    }
    Set-Content -Path (Join-Path $BlogRoot ".npmrc") -Value $npmrc -Encoding UTF8
    if ($NpmRegistry) {
        Write-Info "Using npm registry: $NpmRegistry"
    }

    Push-Location $BlogRoot
    try {
        Write-Info "Running: pnpm install..."
        Write-Info "Installing dependencies, please wait..."
        Write-Host ""
        
        & pnpm install --reporter=default 2>&1 | ForEach-Object { Write-Host $_ }
        
        if ($LASTEXITCODE -ne 0) {
            # 官方源失败时自动用国内镜像重试一次
            if ($NpmRegistry) {
                Write-Warn "pnpm install failed, retrying with $NpmRegistry ..."
                & pnpm install --reporter=default --registry=$NpmRegistry 2>&1 | ForEach-Object { Write-Host $_ }
                if ($LASTEXITCODE -eq 0) {
                    Write-Ok "Dependencies installed (mirror registry)"
                    return
                }
            }
            Write-Err "pnpm install failed with exit code $LASTEXITCODE"
            exit 1
        }
    } finally {
        Pop-Location
    }

    Write-Ok "Dependencies installed"
}

function Build-Blog {
    param([string]$BlogRoot)

    Write-Info "Building blog..."

    $nodeModules = Join-Path $BlogRoot "node_modules"
    if (-not (Test-Path $nodeModules)) {
        Install-BlogDeps $BlogRoot
    }

    # Check package.json scripts
    $packageJsonPath = Join-Path $BlogRoot "package.json"
    if (Test-Path $packageJsonPath) {
        $packageJson = Get-Content $packageJsonPath -Raw | ConvertFrom-Json
        Write-Info "Found package.json with scripts: $($packageJson.scripts | Get-Member -MemberType NoteProperty | Select-Object -ExpandProperty Name)"
    }

    Push-Location $BlogRoot
    try {
        Write-Info "Running: pnpm build..."
        Write-Info "Working directory: $BlogRoot"
        Write-Info "Building, please wait..."
        Write-Host ""
        
        # Run build command with real-time output using ForEach-Object
        & pnpm build --reporter=default 2>&1 | ForEach-Object { Write-Host $_ }
        
        $exitCode = $LASTEXITCODE
        Write-Host ""
        Write-Info "Build process exit code: $exitCode"
        
        if ($exitCode -ne 0) {
            Write-Err "pnpm build failed with exit code $exitCode"
            exit 1
        }
    } finally {
        Pop-Location
    }

    # Check for common output directories
    $outputDirs = @("dist", "build", "out", ".output")
    $foundDir = $null
    
    foreach ($dir in $outputDirs) {
        $outputPath = Join-Path $BlogRoot $dir
        if (Test-Path $outputPath) {
            $foundDir = $outputPath
            Write-Info "Found output directory: $foundDir"
            break
        }
    }

    if (-not $foundDir) {
        Write-Err "Build output directory not found"
        Write-Info "Looking for output directories: $($outputDirs -join ', ')"
        Write-Info "Current directory contents:"
        Get-ChildItem $BlogRoot | Select-Object -ExpandProperty Name
        
        # Check if there's a build error log
        $buildLog = Join-Path $BlogRoot "build.log"
        if (Test-Path $buildLog) {
            Write-Info "Full build log:"
            Get-Content $buildLog
        }
        
        exit 1
    }

    Write-Ok "Blog built successfully: $foundDir"
    return $foundDir
}

function Deploy-Files {
    param([string]$DistDir, [string]$WebRoot)

    Write-Info "Deploying files: $DistDir -> $WebRoot"

    if (-not (Test-Path $WebRoot)) {
        New-Item -ItemType Directory -Path $WebRoot -Force | Out-Null
    }

    if (Test-Path $WebRoot) {
        Remove-Item "$WebRoot\*" -Recurse -Force -ErrorAction SilentlyContinue
    }

    Copy-Item -Path "$DistDir\*" -Destination "$WebRoot\" -Recurse -Force
    Write-Ok "Files deployed: $WebRoot"
}

function Main {
    Write-Host "========================================"
    Write-Host "  AstrBot Firefly Blog Manager"
    Write-Host "  PowerShell Deployment Script v2.0"
    Write-Host "========================================"
    Write-Host "Deploy Mode: $DeployMode"
    Write-Host ""

    switch ($DeployMode) {
        "local_only" {
            Check-NodeJs
            Check-Pnpm
            Check-FireflyProject $LocalBlogRoot
            Install-BlogDeps $LocalBlogRoot
            $distDir = Build-Blog $LocalBlogRoot
            Deploy-Files $distDir $WebRoot
        }
        default {
            Write-Err "Unsupported deploy mode: $DeployMode"
            exit 1
        }
    }

    Write-Host ""
    Write-Host "========================================"
    Write-Ok "Deployment completed!"
    Write-Host "========================================"
}

Main