#requires -Version 5.1
<#
.SYNOPSIS
    AstrBot Firefly Blog Manager - Windows Deploy Script
.DESCRIPTION
    Build and deploy Firefly blog on Windows.
#>

[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# Firefly blog repository (from README)
$FireflyRepo = "https://github.com/qiyueling2716/Firefly-Blog"

# GitHub mirror sites for faster cloning in China
$GithubMirrors = @(
    "https://hubproxy.jiaozi.live/https://github.com/qiyueling2716/Firefly-Blog.git"
)

# Defaults (override via env vars)
$DeployMode = if ($env:DEPLOY_MODE) { $env:DEPLOY_MODE } else { "local_only" }
$LocalBlogRoot = if ($env:LOCAL_BLOG_ROOT) { $env:LOCAL_BLOG_ROOT } else { "D:\www\firefly" }
$WebRoot = if ($env:WEB_ROOT) { $env:WEB_ROOT } else { "D:\www\html" }
$ServerIp = if ($env:SERVER_IP) { $env:SERVER_IP } else { "" }
$ServerPort = if ($env:SERVER_PORT) { [int]$env:SERVER_PORT } else { 22 }
$Username = if ($env:USERNAME) { $env:USERNAME } else { "" }
$AuthType = if ($env:AUTH_TYPE) { $env:AUTH_TYPE } else { "key" }
$PrivateKeyPath = if ($env:PRIVATE_KEY_PATH) { $env:PRIVATE_KEY_PATH } else { "" }
$Password = if ($env:PASSWORD) { $env:PASSWORD } else { "" }
$RemoteBlogRoot = if ($env:REMOTE_BLOG_ROOT) { $env:REMOTE_BLOG_ROOT } else { "/var/www/firefly" }
$RemoteWebRoot = if ($env:REMOTE_WEB_ROOT) { $env:REMOTE_WEB_ROOT } else { "/var/www/html" }

function Write-Info { param([string]$Message) Write-Host ("[INFO] " + $Message) -ForegroundColor Cyan }
function Write-Ok { param([string]$Message) Write-Host ("[OK] " + $Message) -ForegroundColor Green }
function Write-Warn { param([string]$Message) Write-Host ("[WARN] " + $Message) -ForegroundColor Yellow }
function Write-Err { param([string]$Message) Write-Host ("[ERROR] " + $Message) -ForegroundColor Red }

function Test-CommandExists {
    param([string]$Command)
    return [bool](Get-Command $Command -ErrorAction SilentlyContinue)
}

function Test-VersionGe {
    param([string]$Version, [string]$MinVersion)
    return [version]$Version -ge [version]$MinVersion
}

function Test-PythonEnv {
    Write-Info "Checking Python..."
    $script:PythonCmd = $null
    if (Test-CommandExists "python") {
        $script:PythonCmd = "python"
    } elseif (Test-CommandExists "python3") {
        $script:PythonCmd = "python3"
    } else {
        Write-Err "Python not found. Please install Python 3.10+"
        exit 1
    }
    # Use cmd /c to avoid PowerShell output capture issues
    $verOutput = (cmd /c ($script:PythonCmd + " --version") 2>&1) | Out-String
    $verStr = ($verOutput -replace "Python ", "" -replace "python ", "").Trim()
    if ([string]::IsNullOrEmpty($verStr)) {
        Write-Err "Failed to get Python version"
        exit 1
    }
    # Extract major.minor from version string (e.g., "3.12.0rc3" -> "3.12")
    if ($verStr -match "^(\d+\.\d+)") {
        $verStr = $matches[1]
    }
    if (-not (Test-VersionGe $verStr "3.10")) {
        Write-Err ("Python version too old: " + $verStr)
        exit 1
    }
    Write-Ok ("Python " + $verStr)
}

function Test-PipDeps {
    Write-Info "Checking pip dependencies..."
    $reqFile = Join-Path $ScriptDir "requirements.txt"
    if (-not (Test-Path $reqFile)) {
        Write-Warn "requirements.txt not found, skipping"
        return
    }
    $missing = @()
    Get-Content $reqFile | ForEach-Object {
        $line = $_.Trim()
        if ([string]::IsNullOrEmpty($line) -or $line.StartsWith("#")) { return }
        $pkgName = ($line -split '[<>=!~]')[0].Trim()
        $importName = $pkgName
        if ($pkgName -eq "pyyaml") { $importName = "yaml" }
        $checkScript = "try:" + [Environment]::NewLine + "    import " + $importName + [Environment]::NewLine + "except:" + [Environment]::NewLine + "    exit(1)"
        $null = & $script:PythonCmd -c $checkScript 2>$null
        if ($LASTEXITCODE -ne 0) { $missing += $line }
    }
    if ($missing.Count -eq 0) {
        Write-Ok "All pip dependencies installed"
        return
    }
    Write-Warn ("Missing: " + ($missing -join ", "))
    Write-Info "Installing missing dependencies..."
    $pipCmd = $null
    if (Test-CommandExists "pip") { $pipCmd = "pip" }
    elseif (Test-CommandExists "pip3") { $pipCmd = "pip3" }
    else {
        Write-Err "pip not found"
        exit 1
    }
    foreach ($pkg in $missing) {
        Write-Info ("Installing " + $pkg + "...")
        & $pipCmd install $pkg
        if ($LASTEXITCODE -ne 0) {
            Write-Err ("Failed to install " + $pkg)
            exit 1
        }
    }
    Write-Ok "Pip dependencies installed"
}

function Test-NodejsEnv {
    Write-Info "Checking Node.js..."
    if (-not (Test-CommandExists "node")) {
        Write-Warn "Node.js not found. Attempting to install..."
        Install-Nodejs
    }
    $verStr = (node --version) -replace "v", ""
    if (-not (Test-VersionGe $verStr "22.0.0")) {
        Write-Err ("Node.js version too old: " + $verStr)
        exit 1
    }
    Write-Ok ("Node.js " + $verStr)
}

function Install-Nodejs {
    # Try winget first (without specific version, let it pick latest LTS)
    if (Test-CommandExists "winget") {
        Write-Info "Installing Node.js via winget..."
        winget install OpenJS.NodeJS --accept-source-agreements --accept-package-agreements --silent
        if ($LASTEXITCODE -eq 0) {
            Write-Ok "Node.js installed via winget"
            # Refresh PATH
            $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
            return
        }
        Write-Warn "winget install failed, trying alternative method..."
    }
    # Try chocolatey
    if (Test-CommandExists "choco") {
        Write-Info "Installing Node.js via chocolatey..."
        choco install nodejs -y
        if ($LASTEXITCODE -eq 0) {
            Write-Ok "Node.js installed via chocolatey"
            $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
            return
        }
        Write-Warn "choco install failed"
    }
    Write-Err "Could not auto-install Node.js. Please install manually from https://nodejs.org/"
    exit 1
}

function Test-PnpmEnv {
    Write-Info "Checking pnpm..."
    if (-not (Test-CommandExists "pnpm")) {
        Write-Warn "pnpm not found. Attempting to install..."
        Install-Pnpm
    }
    $verStr = pnpm --version
    Write-Ok ("pnpm " + $verStr)
}

function Install-Pnpm {
    if (Test-CommandExists "npm") {
        Write-Info "Installing pnpm via npm..."
        npm install -g pnpm
        if ($LASTEXITCODE -eq 0) {
            Write-Ok "pnpm installed"
            return
        }
    }
    if (Test-CommandExists "corepack") {
        Write-Info "Installing pnpm via corepack..."
        corepack enable
        corepack prepare pnpm@latest --activate
        if ($LASTEXITCODE -eq 0) {
            Write-Ok "pnpm installed via corepack"
            return
        }
    }
    Write-Err "Could not auto-install pnpm. Please install manually: npm install -g pnpm"
    exit 1
}

function Test-FireflyProject {
    param([string]$BlogRoot)
    Write-Info "Checking Firefly project..."
    if (-not (Test-Path $BlogRoot)) {
        Write-Warn ("Blog directory not found: " + $BlogRoot)
        Write-Info "Creating directory..."
        New-Item -ItemType Directory -Path $BlogRoot -Force | Out-Null
        Write-Ok ("Created: " + $BlogRoot)
    }
    # Check if directory is empty (no package.json)
    $pkgJson = Join-Path $BlogRoot "package.json"
    if (-not (Test-Path $pkgJson)) {
        Write-Warn "Blog directory is empty, cloning Firefly repository..."
        if (Test-CommandExists "git") {
            Push-Location (Split-Path $BlogRoot -Parent)
            try {
                $repoName = Split-Path $FireflyRepo -Leaf
                $cloneSuccess = $false

                # Try mirror sites first for faster cloning
                foreach ($mirrorUrl in $GithubMirrors) {
                    Write-Info ("Trying mirror: " + $mirrorUrl)
                    git clone $mirrorUrl
                    if ($LASTEXITCODE -eq 0) {
                        $clonedDir = Join-Path (Split-Path $BlogRoot -Parent) $repoName
                        if (Test-Path $clonedDir) {
                            Get-ChildItem $clonedDir | Move-Item -Destination $BlogRoot -Force
                            Remove-Item $clonedDir -Recurse -Force
                            Write-Ok ("Firefly blog cloned via " + $mirror)
                            $cloneSuccess = $true
                            break
                        }
                    }
                    # Clean up failed clone
                    $clonedDir = Join-Path (Split-Path $BlogRoot -Parent) $repoName
                    if (Test-Path $clonedDir) { Remove-Item $clonedDir -Recurse -Force -ErrorAction SilentlyContinue }
                }

                # Fallback to original GitHub
                if (-not $cloneSuccess) {
                    Write-Info ("Trying original: " + $FireflyRepo)
                    git clone $FireflyRepo
                    if ($LASTEXITCODE -eq 0) {
                        $clonedDir = Join-Path (Split-Path $BlogRoot -Parent) $repoName
                        if (Test-Path $clonedDir) {
                            Get-ChildItem $clonedDir | Move-Item -Destination $BlogRoot -Force
                            Remove-Item $clonedDir -Recurse -Force
                            Write-Ok "Firefly blog cloned from GitHub"
                            $cloneSuccess = $true
                        }
                    }
                }

                if (-not $cloneSuccess) {
                    Write-Warn "All clone attempts failed"
                    Write-Warn "Please clone manually: git clone $FireflyRepo $BlogRoot"
                }
            } finally {
                Pop-Location
            }
        } else {
            Write-Warn "git not found, please install Git or clone manually"
            Write-Warn "  git clone $FireflyRepo $BlogRoot"
        }
    }
    # Final check after clone attempt
    $pkgJson = Join-Path $BlogRoot "package.json"
    if (-not (Test-Path $pkgJson)) {
        Write-Err "package.json not found. Not a valid Firefly project."
        exit 1
    }
    $postsDir = Join-Path $BlogRoot "src\content\posts"
    if (-not (Test-Path $postsDir)) {
        Write-Warn "src/content/posts not found"
    }
    Write-Ok ("Firefly project OK: " + $BlogRoot)
}

function Install-BlogDeps {
    param([string]$BlogRoot)
    Write-Info "Installing blog dependencies (pnpm install)..."
    $nodeModules = Join-Path $BlogRoot "node_modules"
    if (Test-Path $nodeModules) {
        Write-Warn "node_modules exists, skipping"
        return
    }
    Push-Location $BlogRoot
    try {
        pnpm install
        if ($LASTEXITCODE -ne 0) {
            Write-Err "pnpm install failed"
            exit 1
        }
    } finally {
        Pop-Location
    }
    Write-Ok "Blog dependencies installed"
}

function Build-Blog {
    param([string]$BlogRoot)
    Write-Info "Building blog (pnpm build)..."
    $nodeModules = Join-Path $BlogRoot "node_modules"
    if (-not (Test-Path $nodeModules)) {
        Write-Warn "node_modules not found, installing first..."
        Install-BlogDeps $BlogRoot
    }
    Push-Location $BlogRoot
    try {
        pnpm build
        if ($LASTEXITCODE -ne 0) {
            Write-Err "pnpm build failed"
            exit 1
        }
    } finally {
        Pop-Location
    }
    $distDir = Join-Path $BlogRoot "dist"
    if (-not (Test-Path $distDir)) {
        Write-Err "dist directory not found after build"
        exit 1
    }
    Write-Ok ("Build complete: " + $distDir)
}

function Deploy-Local {
    $distDir = Join-Path $LocalBlogRoot "dist"
    Write-Info ("Local deploy: " + $distDir + " -> " + $WebRoot)
    if (-not (Test-Path $distDir)) {
        Write-Err "Build output not found"
        exit 1
    }
    # Ensure web root parent directory exists
    $webRootParent = Split-Path $WebRoot -Parent
    if (-not (Test-Path $webRootParent)) {
        Write-Info ("Creating web root parent: " + $webRootParent)
        New-Item -ItemType Directory -Path $webRootParent -Force | Out-Null
    }
    if (Test-Path $WebRoot) {
        $items = Get-ChildItem $WebRoot -ErrorAction SilentlyContinue
        if ($items) {
            $backupDir = $WebRoot + ".backup." + (Get-Date -Format "yyyyMMddHHmmss")
            Write-Info ("Backing up to " + $backupDir)
            Copy-Item $WebRoot $backupDir -Recurse -Force
        }
    }
    if (Test-Path $WebRoot) {
        Remove-Item $WebRoot -Recurse -Force
    }
    Copy-Item $distDir $WebRoot -Recurse -Force
    Write-Ok ("Deployed to " + $WebRoot)
}

function Deploy-LocalToRemote {
    $distDir = Join-Path $LocalBlogRoot "dist"
    Write-Info ("Deploy to remote: " + $distDir + " -> " + $ServerIp + ":" + $RemoteWebRoot)
    if (-not (Test-Path $distDir)) {
        Write-Err "Build output not found"
        exit 1
    }
    if ([string]::IsNullOrEmpty($ServerIp) -or [string]::IsNullOrEmpty($Username)) {
        Write-Err "SERVER_IP and USERNAME required for remote deploy"
        exit 1
    }
    $sshOpts = "-P " + $ServerPort + " -o StrictHostKeyChecking=no -o ConnectTimeout=10"
    if ($AuthType -eq "key" -and -not [string]::IsNullOrEmpty($PrivateKeyPath) -and (Test-Path $PrivateKeyPath)) {
        $sshOpts = $sshOpts + " -i `"" + $PrivateKeyPath + "`""
    }
    Write-Info "Clearing remote directory..."
    if ($AuthType -eq "password" -and -not [string]::IsNullOrEmpty($Password)) {
        Write-Warn "Password auth on Windows: use key auth instead"
        Write-Warn ("Or run manually: ssh " + $sshOpts + " " + $Username + "@" + $ServerIp + " `"rm -rf " + $RemoteWebRoot + "/*`"")
    } else {
        ssh $sshOpts ($Username + "@" + $ServerIp) ("rm -rf " + $RemoteWebRoot + "/*")
    }
    Write-Info "Deploying via scp..."
    if ($AuthType -eq "password" -and -not [string]::IsNullOrEmpty($Password)) {
        Write-Warn "scp with password requires interactive input"
        Write-Info "Run manually:"
        Write-Host ("  scp -r " + $sshOpts + " `"" + $distDir + "\*`" `"" + $Username + "@" + $ServerIp + ":" + $RemoteWebRoot + "/`"")
        exit 1
    } else {
        scp -r $sshOpts ($distDir + "\*") ($Username + "@" + $ServerIp + ":" + $RemoteWebRoot + "/")
        if ($LASTEXITCODE -ne 0) {
            Write-Err "scp deploy failed"
            exit 1
        }
    }
    Write-Ok ("Deployed to " + $ServerIp + ":" + $RemoteWebRoot)
}

function Deploy-Remote {
    Write-Info ("Remote build and deploy: " + $ServerIp + ":" + $RemoteBlogRoot + " -> " + $RemoteWebRoot)
    if ([string]::IsNullOrEmpty($ServerIp) -or [string]::IsNullOrEmpty($Username)) {
        Write-Err "SERVER_IP and USERNAME required"
        exit 1
    }
    $sshOpts = "-P " + $ServerPort + " -o StrictHostKeyChecking=no -o ConnectTimeout=10"
    if ($AuthType -eq "key" -and -not [string]::IsNullOrEmpty($PrivateKeyPath) -and (Test-Path $PrivateKeyPath)) {
        $sshOpts = $sshOpts + " -i `"" + $PrivateKeyPath + "`""
    }
    $nl = [Environment]::NewLine
    $remoteCmds = "set -e" + $nl
    $remoteCmds += "cd " + $RemoteBlogRoot + $nl
    $remoteCmds += "if [ ! -d node_modules ]; then" + $nl
    $remoteCmds += "    echo Installing dependencies..." + $nl
    $remoteCmds += "    pnpm install" + $nl
    $remoteCmds += "fi" + $nl
    $remoteCmds += "echo Building..." + $nl
    $remoteCmds += "pnpm build" + $nl
    $remoteCmds += "echo Deploying..." + $nl
    $remoteCmds += "rm -rf " + $RemoteWebRoot + "/*" + $nl
    $remoteCmds += "cp -r " + $RemoteBlogRoot + "/dist/* " + $RemoteWebRoot + "/" + $nl
    $remoteCmds += "echo Done"
    Write-Info "Running remote build..."
    if ($AuthType -eq "password" -and -not [string]::IsNullOrEmpty($Password)) {
        Write-Warn "Password auth on Windows: use key auth instead"
        Write-Info "SSH to server and run:"
        Write-Host $remoteCmds
        exit 1
    } else {
        ssh $sshOpts ($Username + "@" + $ServerIp) $remoteCmds
        if ($LASTEXITCODE -ne 0) {
            Write-Err "Remote build/deploy failed"
            exit 1
        }
    }
    Write-Ok "Remote build/deploy complete"
}

Write-Host "========================================"
Write-Host "  AstrBot Firefly Blog Manager Deploy"
Write-Host "========================================"
Write-Host
Write-Host ("Deploy mode: " + $DeployMode)
Write-Host

$configFile = Join-Path $ScriptDir "deploy.conf"
if (Test-Path $configFile) {
    Write-Info ("Loading config: " + $configFile)
    Get-Content $configFile | ForEach-Object {
        $line = $_.Trim()
        if ([string]::IsNullOrEmpty($line) -or $line.StartsWith("#")) { return }
        if ($line -match "^([^=]+)=(.*)$") {
            $key = $matches[1].Trim()
            $value = $matches[2].Trim()
            [Environment]::SetEnvironmentVariable($key, $value, "Process")
            switch ($key) {
                "DEPLOY_MODE" { $script:DeployMode = $value }
                "LOCAL_BLOG_ROOT" { $script:LocalBlogRoot = $value }
                "WEB_ROOT" { $script:WebRoot = $value }
                "SERVER_IP" { $script:ServerIp = $value }
                "SERVER_PORT" { $script:ServerPort = [int]$value }
                "USERNAME" { $script:Username = $value }
                "AUTH_TYPE" { $script:AuthType = $value }
                "PRIVATE_KEY_PATH" { $script:PrivateKeyPath = $value }
                "PASSWORD" { $script:Password = $value }
                "REMOTE_BLOG_ROOT" { $script:RemoteBlogRoot = $value }
                "REMOTE_WEB_ROOT" { $script:RemoteWebRoot = $value }
            }
        }
    }
}

Test-PythonEnv
Test-PipDeps
Test-NodejsEnv
Test-PnpmEnv

switch ($DeployMode) {
    "local_only" {
        Test-FireflyProject $LocalBlogRoot
        Install-BlogDeps $LocalBlogRoot
        Build-Blog $LocalBlogRoot
        Deploy-Local
    }
    "local_build" {
        Test-FireflyProject $LocalBlogRoot
        Install-BlogDeps $LocalBlogRoot
        Build-Blog $LocalBlogRoot
        Deploy-LocalToRemote
    }
    "remote_build" {
        if ([string]::IsNullOrEmpty($ServerIp) -or [string]::IsNullOrEmpty($Username)) {
            Write-Err "remote_build requires SERVER_IP and USERNAME"
            exit 1
        }
        Deploy-Remote
    }
    default {
        Write-Err ("Unknown deploy mode: " + $DeployMode)
        exit 1
    }
}

Write-Host
Write-Host "========================================"
Write-Ok "Deploy complete!"
Write-Host "========================================"
