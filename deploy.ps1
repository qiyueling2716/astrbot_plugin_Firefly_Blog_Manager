#requires -Version 5.1

param(
    [string]$ConfigFile
)

$SCRIPT_DIR = $PSScriptRoot
$FIREFLY_REPO = "https://github.com/qiyueling2716/Firefly-Blog"

$DeployMode = if ($env:DEPLOY_MODE) { $env:DEPLOY_MODE } else { "local_only" }
$LocalBlogRoot = if ($env:LOCAL_BLOG_ROOT) { $env:LOCAL_BLOG_ROOT } else { "D:\www\firefly" }
$WebRoot = if ($env:WEB_ROOT) { $env:WEB_ROOT } else { "D:\www\firefly" }

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
            Write-Info "Installing pnpm..."
            npm install -g pnpm
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
            Write-Info "Cloning $FIREFLY_REPO to $BlogRoot"
            git clone $FIREFLY_REPO $BlogRoot 2>&1 | Out-Null
            if ($LASTEXITCODE -eq 0) {
                Write-Ok "Firefly cloned successfully"
            } else {
                Write-Err "Failed to clone repository"
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

    Push-Location $BlogRoot
    try {
        Write-Info "Running: pnpm install..."
        Write-Info "Installing dependencies, please wait..."
        Write-Host ""
        
        & pnpm install --reporter=default 2>&1 | ForEach-Object { Write-Host $_ }
        
        if ($LASTEXITCODE -ne 0) {
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
    Write-Host "  PowerShell Deployment Script v1.0"
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