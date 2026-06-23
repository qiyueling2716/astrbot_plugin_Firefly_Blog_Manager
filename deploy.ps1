#requires -Version 5.1

# =============================================================================
# AstrBot Firefly Blog Manager - PowerShell 部署脚本
# =============================================================================
# 功能：
#   1. 自动检测本地已部署的 Firefly 博客目录
#   2. 检测 Python 版本（>= 3.10）
#   3. 检测并安装 pip 依赖（asyncssh, pyyaml）
#   4. 检测 Node.js 版本（>= 22）
#   5. 检测 pnpm
#   6. 检测 Firefly 博客项目结构
#   7. 安装博客依赖（pnpm install）
#   8. 构建博客（pnpm build）
#   9. 根据部署模式执行对应部署操作
#   10. 自动配置 Nginx 或 Apache
#   11. 支持 SSL 证书配置
# =============================================================================

param(
    [string]$ConfigFile
)

# 颜色定义
$ESC = [char]27
$RED = "$ESC[91m"
$GREEN = "$ESC[92m"
$YELLOW = "$ESC[93m"
$BLUE = "$ESC[94m"
$CYAN = "$ESC[96m"
$NC = "$ESC[0m"

$SCRIPT_DIR = $PSScriptRoot
$PLUGIN_NAME = "astrbot_plugin_Firefly_Blog_Manager"

# Firefly blog repository
$FIREFLY_REPO = "https://github.com/qiyueling2716/Firefly-Blog"

# GitHub mirror sites for faster cloning in China
$GITHUB_MIRRORS = @(
    "https://hubproxy.jiaozi.live/https://github.com",
    "https://ghproxy.com/https://github.com"
)

# =============================================================================
# 默认配置（可通过环境变量或 deploy.conf 覆盖）
# =============================================================================

# 部署模式: local_only | local_build | remote_build
$DeployMode = if ($env:DEPLOY_MODE) { $env:DEPLOY_MODE } else { "local_only" }

# 博客目录配置
$LocalBlogRoot = if ($env:LOCAL_BLOG_ROOT) { $env:LOCAL_BLOG_ROOT } else { "D:\www\firefly" }
$WebRoot = if ($env:WEB_ROOT) { $env:WEB_ROOT } else { "D:\www\firefly" }

# 域名配置
$DomainName = if ($env:DOMAIN_NAME) { $env:DOMAIN_NAME } else { "" }
$EnableHttps = if ($env:ENABLE_HTTPS) { $env:ENABLE_HTTPS } else { "false" }
$SslCertPath = if ($env:SSL_CERT_PATH) { $env:SSL_CERT_PATH } else { "" }
$SslCertKeyPath = if ($env:SSL_CERT_KEY_PATH) { $env:SSL_CERT_KEY_PATH } else { "" }

# Web 服务器配置: nginx | apache | none
$WebServer = if ($env:WEB_SERVER) { $env:WEB_SERVER } else { "nginx" }
$NginxConfigTemplate = if ($env:NGINX_CONFIG_TEMPLATE) { $env:NGINX_CONFIG_TEMPLATE } else { "" }
$ApacheVhostConfig = if ($env:APACHE_VHOST_CONFIG) { $env:APACHE_VHOST_CONFIG } else { "" }

# 远端服务器配置
$ServerIp = if ($env:SERVER_IP) { $env:SERVER_IP } else { "" }
$ServerPort = if ($env:SERVER_PORT) { [int]$env:SERVER_PORT } else { 22 }
$Username = if ($env:USERNAME) { $env:USERNAME } else { "" }
$AuthType = if ($env:AUTH_TYPE) { $env:AUTH_TYPE } else { "key" }
$PrivateKeyPath = if ($env:PRIVATE_KEY_PATH) { $env:PRIVATE_KEY_PATH } else { "" }
$Password = if ($env:PASSWORD) { $env:PASSWORD } else { "" }
$RemoteBlogRoot = if ($env:REMOTE_BLOG_ROOT) { $env:REMOTE_BLOG_ROOT } else { "D:\www\firefly" }
$RemoteWebRoot = if ($env:REMOTE_WEB_ROOT) { $env:REMOTE_WEB_ROOT } else { "D:\www\firefly" }

# 高级选项
$GitHubMirror = if ($env:GITHUB_MIRROR) { $env:GITHUB_MIRROR } else { "" }
$AutoBackup = if ($env:AUTO_BACKUP) { $env:AUTO_BACKUP } else { "true" }
$BackupRetentionDays = if ($env:BACKUP_RETENTION_DAYS) { [int]$env:BACKUP_RETENTION_DAYS } else { 7 }

# =============================================================================
# 辅助函数
# =============================================================================

function Write-Info {
    Write-Host "${BLUE}[INFO]${NC} $args" -NoNewline
    Write-Host ""
}

function Write-Ok {
    Write-Host "${GREEN}[OK]${NC} $args" -NoNewline
    Write-Host ""
}

function Write-Warn {
    Write-Host "${YELLOW}[WARN]${NC} $args" -NoNewline
    Write-Host ""
}

function Write-Err {
    Write-Host "${RED}[ERROR]${NC} $args" -NoNewline
    Write-Host ""
}

function Write-Step {
    Write-Host "${CYAN}[STEP]${NC} $args" -NoNewline
    Write-Host ""
}

function Test-CommandExists {
    param([string]$Command)
    $null = Get-Command $Command -ErrorAction SilentlyContinue
    return $?
}

# =============================================================================
# 加载配置文件
# =============================================================================

function Load-Config {
    param([string]$ConfigPath)

    if (-not (Test-Path $ConfigPath)) {
        Write-Info "配置文件不存在: $ConfigPath，跳过"
        return
    }

    Write-Info "加载配置文件: $ConfigPath"

    # 解析 INI 格式配置文件
    $content = Get-Content $ConfigPath -Raw
    $lines = $content -split "`r?`n"

    foreach ($line in $lines) {
        # 跳过注释和空行
        if ($line -match '^\s*#' -or $line -match '^\s*$') {
            continue
        }

        # 解析键值对
        if ($line -match '^\s*(\w+)\s*=\s*(.*)$') {
            $key = $matches[1]
            $value = $matches[2].Trim()

            # 设置环境变量供后续使用
            $envKey = $key -replace '_', '_'
            Set-Variable -Name $key -Value $value -ErrorAction SilentlyContinue
        }
    }
}

# =============================================================================
# 1. 检测 Python 环境
# =============================================================================

function Check-Python {
    Write-Step "1. 检测 Python 环境..."

    if (-not (Test-CommandExists "python3")) {
        Write-Err "未找到 python3，请先安装 Python 3.10 或更高版本"
        Write-Info "安装方式: https://www.python.org/downloads/"
        exit 1
    }

    $pythonVersion = python3 -c "import sys; print('.'.join(map(str, sys.version_info[:2])))"
    $versionParts = $pythonVersion -split '\.'
    $major = [int]$versionParts[0]
    $minor = [int]$versionParts[1]

    if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 10)) {
        Write-Err "Python 版本过低: $pythonVersion，需要 >= 3.10"
        exit 1
    }

    Write-Ok "Python $pythonVersion"
}

# =============================================================================
# 2. 检测并安装 pip 依赖
# =============================================================================

function Check-PipDeps {
    Write-Step "2. 检测 pip 依赖..."

    $reqFile = Join-Path $SCRIPT_DIR "requirements.txt"
    if (-not (Test-Path $reqFile)) {
        Write-Warn "未找到 requirements.txt，跳过 pip 依赖检查"
        return
    }

    $missing = @()
    $lines = Get-Content $reqFile

    foreach ($line in $lines) {
        $line = $line.Trim()
        if ([string]::IsNullOrEmpty($line) -or $line -match '^\s*#') {
            continue
        }

        $pkgName = $line -replace '[<>=!~].*', ''

        try {
            python3 -c "import $pkgName" 2>$null | Out-Null
            if ($LASTEXITCODE -ne 0) {
                $missing += $line
            }
        } catch {
            $missing += $line
        }
    }

    if ($missing.Count -eq 0) {
        Write-Ok "所有 pip 依赖已安装"
        return
    }

    Write-Warn "以下依赖未安装: $($missing -join ', ')"
    Write-Info "正在安装缺失的依赖..."

    if (-not (Test-CommandExists "pip3")) {
        Write-Err "未找到 pip3，无法安装依赖"
        exit 1
    }

    foreach ($pkg in $missing) {
        Write-Info "安装 $pkg ..."
        if (-not (pip3 install $pkg)) {
            Write-Err "安装 $pkg 失败"
            exit 1
        }
    }

    Write-Ok "pip 依赖安装完成"
}

# =============================================================================
# 3. 检测 Node.js
# =============================================================================

function Install-NodeJs {
    Write-Step "安装 Node.js 22..."

    # 优先使用 winget 安装（Windows 10 1709+ / Windows 11 自带）
    if (Test-CommandExists "winget") {
        Write-Info "使用 winget 安装 Node.js..."
        
        try {
            # 使用 winget 安装 Node.js LTS 22.x
            $process = Start-Process -FilePath "winget" -ArgumentList "install --id OpenJS.NodeJS.LTS --silent --accept-package-agreements --accept-source-agreements" -Wait -PassThru -NoNewWindow
            
            if ($process.ExitCode -eq 0 -or $process.ExitCode -eq 1618) {  # 1618 = 已安装
                Write-Ok "Node.js 安装完成（或已安装）"
                
                # 刷新 PATH
                $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("PATH", "User")
                return $true
            } else {
                Write-Warn "winget 安装失败，退出码: $($process.ExitCode)，尝试其他方式..."
            }
        } catch {
            Write-Warn "winget 安装失败: $_，尝试其他方式..."
        }
    }

    # 备用方案：下载 MSI 安装包
    Write-Info "winget 不可用或安装失败，尝试直接下载安装..."
    
    $downloadUrls = @(
        "https://nodejs.org/dist/v22.11.0/node-v22.11.0-x64.msi",
        "https://mirrors.tuna.tsinghua.edu.cn/nodejs-release/v22.11.0/node-v22.11.0-x64.msi",
        "https://cdn.npmmirror.com/binaries/node/v22.11.0/node-v22.11.0-x64.msi"
    )

    $installerPath = Join-Path $env:TEMP "node-v22.11.0-x64.msi"
    $downloadSuccess = $false

    foreach ($url in $downloadUrls) {
        try {
            Write-Info "尝试从 $url 下载..."
            
            $webClient = New-Object System.Net.WebClient
            $webClient.Headers.Add("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
            $webClient.Timeout = 300000  # 5分钟超时
            
            $webClient.DownloadFile($url, $installerPath)
            $downloadSuccess = $true
            Write-Ok "下载成功"
            break
        } catch {
            Write-Warn "从 $url 下载失败: $_"
        }
    }

    if (-not $downloadSuccess) {
        Write-Err "无法从所有源下载 Node.js"
        return $false
    }

    if (-not (Test-Path $installerPath)) {
        Write-Err "下载的安装包不存在"
        return $false
    }

    Write-Info "安装 Node.js..."
    $process = Start-Process -FilePath "msiexec.exe" -ArgumentList "/i `"$installerPath`" /qn /norestart" -Wait -PassThru -NoNewWindow
    
    if ($process.ExitCode -ne 0) {
        Write-Err "Node.js 安装失败，退出码: $($process.ExitCode)"
        Write-Info "请尝试手动安装 Node.js"
        Write-Info "下载地址: https://nodejs.org/"
        return $false
    }

    Write-Ok "Node.js 安装完成"
    
    # 刷新 PATH
    $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("PATH", "User")
    
    return $true
}

function Check-NodeJs {
    Write-Step "3. 检测 Node.js 环境..."

    if (-not (Test-CommandExists "node")) {
        Write-Warn "未找到 Node.js"
        Write-Info "正在自动安装 Node.js 22..."
        
        if (-not (Install-NodeJs)) {
            Write-Err "无法自动安装 Node.js，请手动安装"
            Write-Info "下载地址: https://nodejs.org/"
            exit 1
        }
    }

    # 再次检查
    if (-not (Test-CommandExists "node")) {
        Write-Err "安装后仍未找到 Node.js，请重启终端或检查 PATH"
        exit 1
    }

    $nodeVersion = node --version
    $nodeVersion = $nodeVersion -replace 'v', ''
    $versionParts = $nodeVersion -split '\.'
    $major = [int]$versionParts[0]
    $minor = [int]$versionParts[1]

    if ($major -lt 22) {
        Write-Warn "Node.js 版本过低: $nodeVersion，需要 >= 22"
        Write-Info "正在自动升级 Node.js..."
        
        if (-not (Install-NodeJs)) {
            Write-Err "无法自动升级 Node.js，请手动安装"
            Write-Info "下载地址: https://nodejs.org/"
            exit 1
        }
    }

    Write-Ok "Node.js $nodeVersion"
}

# =============================================================================
# 4. 检测 pnpm
# =============================================================================

function Check-Pnpm {
    Write-Step "4. 检测 pnpm..."

    if (-not (Test-CommandExists "pnpm")) {
        Write-Warn "未找到 pnpm，尝试通过 npm 安装..."
        if (Test-CommandExists "npm") {
            npm install -g pnpm
            if ($LASTEXITCODE -ne 0) {
                Write-Err "npm 安装 pnpm 失败"
                exit 1
            }
            # 安装后刷新 PATH，使 pnpm 立即可用
            $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("PATH", "User")
        } else {
            Write-Err "未找到 npm，无法安装 pnpm"
            exit 1
        }
    }

    # 再次检查 pnpm 是否可用
    if (-not (Test-CommandExists "pnpm")) {
        # 尝试直接使用 node_modules 中的 pnpm
        $npmPrefix = npm prefix -g 2>$null
        if ($LASTEXITCODE -eq 0 -and $npmPrefix) {
            $pnpmPath = Join-Path $npmPrefix "pnpm.cmd"
            if (Test-Path $pnpmPath) {
                Write-Info "使用全局安装的 pnpm: $pnpmPath"
                $script:PNPM_CMD = $pnpmPath
                $pnpmVersion = & $pnpmPath --version
                Write-Ok "pnpm $pnpmVersion"
                return
            }
        }
        Write-Err "安装 pnpm 后仍然无法找到，请手动安装 pnpm"
        exit 1
    }

    $script:PNPM_CMD = "pnpm"
    $pnpmVersion = pnpm --version
    Write-Ok "pnpm $pnpmVersion"
}

# =============================================================================
# 5. 检测 Firefly 博客项目结构
# =============================================================================

function Check-FireflyProject {
    param([string]$BlogRoot)

    Write-Step "5. 检测 Firefly 博客项目..."

    if (-not (Test-Path $BlogRoot)) {
        Write-Warn "博客目录不存在: $BlogRoot"
        Write-Info "正在创建目录..."
        New-Item -ItemType Directory -Path $BlogRoot -Force | Out-Null
        Write-Ok "已创建: $BlogRoot"
    }

    $packageJson = Join-Path $BlogRoot "package.json"
    if (-not (Test-Path $packageJson)) {
        Write-Warn "博客目录为空，正在克隆 Firefly 仓库..."

        if (Test-CommandExists "git") {
            $parentDir = Split-Path $BlogRoot -Parent
            if (-not (Test-Path $parentDir)) {
                New-Item -ItemType Directory -Path $parentDir -Force | Out-Null
            }

            $cloneUrl = $FIREFLY_REPO
            if ($GitHubMirror) {
                $cloneUrl = "$GitHubMirror/qiyueling2716/Firefly-Blog.git"
                Write-Info "使用镜像: $cloneUrl"
            }

            try {
                git clone $cloneUrl $BlogRoot 2>&1 | Out-Null
                if ($LASTEXITCODE -eq 0) {
                    Write-Ok "Firefly 博客克隆成功"
                } else {
                    throw "克隆失败"
                }
            } catch {
                # 尝试官方地址
                Write-Info "尝试使用官方地址..."
                git clone $FIREFLY_REPO $BlogRoot 2>&1 | Out-Null
                if ($LASTEXITCODE -ne 0) {
                    Write-Err "克隆失败，请手动执行: git clone $FIREFLY_REPO $BlogRoot"
                    exit 1
                }
                Write-Ok "Firefly 博客克隆成功"
            }
        } else {
            Write-Err "未找到 git，请安装 Git 或手动克隆"
            Write-Info "  下载地址: https://git-scm.com/"
            exit 1
        }
    }

    if (-not (Test-Path $packageJson)) {
        Write-Err "目录 $BlogRoot 下未找到 package.json"
        exit 1
    }

    Write-Ok "Firefly 项目验证通过: $BlogRoot"
}

# =============================================================================
# 资源监控函数
# =============================================================================

function Get-CpuUsage {
    # 获取 CPU 使用率（百分比）
    $cpuInfo = Get-WmiObject Win32_Processor
    $totalCores = $cpuInfo.NumberOfLogicalProcessors
    $cpuLoad = 0
    
    foreach ($cpu in $cpuInfo) {
        $cpuLoad += $cpu.LoadPercentage
    }
    
    return [math]::Round($cpuLoad / $totalCores)
}

function Get-MemoryUsage {
    # 获取内存使用率（百分比）
    $memInfo = Get-WmiObject Win32_OperatingSystem
    $totalMemory = $memInfo.TotalVisibleMemorySize
    $freeMemory = $memInfo.FreePhysicalMemory
    
    $usedMemory = $totalMemory - $freeMemory
    return [math]::Round(($usedMemory / $totalMemory) * 100)
}

function Check-ResourceWarning {
    param(
        [int]$CpuThreshold = 80,
        [int]$MemThreshold = 85
    )
    
    $cpuUsage = Get-CpuUsage
    $memUsage = Get-MemoryUsage
    
    Write-Info "当前资源使用: CPU ${cpuUsage}%, 内存 ${memUsage}%"
    
    $hasWarning = $false
    
    if ($cpuUsage -ge $CpuThreshold) {
        Write-Warn "⚠️ CPU 使用率 ${cpuUsage}% 超过警告阈值 ${cpuThreshold}%"
        Write-Warn "  建议：关闭不必要的进程或增加 CPU 资源"
        $hasWarning = $true
    }
    
    if ($memUsage -ge $MemThreshold) {
        Write-Warn "⚠️ 内存使用率 ${memUsage}% 超过警告阈值 ${MemThreshold}%"
        Write-Warn "  建议：关闭不必要的进程或增加内存资源"
        Write-Warn "  构建过程可能会进一步增加内存消耗，可能导致内存不足"
        $hasWarning = $true
    }
    
    return $hasWarning
}

$script:monitorRunning = $false
$script:monitorJob = $null

function Start-ResourceMonitor {
    param([string]$BlogRoot)
    
    $logFile = Join-Path $BlogRoot "build_resource_monitor.log"
    
    # 创建监控日志文件
    "=== 构建资源监控日志 ===" | Out-File -FilePath $logFile -Encoding utf8
    "开始时间: $(Get-Date)" | Out-File -FilePath $logFile -Encoding utf8 -Append
    
    $script:monitorRunning = $true
    
    $script:monitorJob = Start-Job -ScriptBlock {
        param($logFile)
        
        while ($true) {
            $timestamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
            $cpu = (Get-WmiObject Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average
            $memInfo = Get-WmiObject Win32_OperatingSystem
            $memUsage = [math]::Round(((($memInfo.TotalVisibleMemorySize - $memInfo.FreePhysicalMemory) / $memInfo.TotalVisibleMemorySize) * 100))
            
            "[$timestamp] CPU: ${cpu}%, 内存: ${memUsage}%" | Out-File -FilePath $logFile -Encoding utf8 -Append
            
            Start-Sleep -Seconds 2
        }
    } -ArgumentList $logFile
    
    Write-Info "资源监控已启动"
}

function Stop-ResourceMonitor {
    if ($script:monitorRunning -and $script:monitorJob) {
        Stop-Job $script:monitorJob
        Remove-Job $script:monitorJob
        $script:monitorJob = $null
        $script:monitorRunning = $false
        Write-Info "资源监控已停止"
    }
}

# =============================================================================
# 6. 安装博客依赖
# =============================================================================

function Install-BlogDeps {
    param([string]$BlogRoot)

    Write-Step "6. 安装博客依赖 (pnpm install)..."

    # 检查资源使用情况
    $resourceWarning = Check-ResourceWarning
    
    if ($resourceWarning) {
        Write-Warn "资源使用较高，继续安装依赖可能会影响系统性能"
        $continue = Read-Host "是否继续安装? (Y/N)"
        if ($continue -ne 'Y' -and $continue -ne 'y') {
            Write-Info "用户取消安装"
            exit 0
        }
    }

    $nodeModules = Join-Path $BlogRoot "node_modules"
    if (Test-Path $nodeModules) {
        Write-Info "node_modules 已存在，跳过安装"
        return
    }

    Push-Location $BlogRoot
    try {
        Write-Info "执行: $PNPM_CMD install"
        if (-not (& $PNPM_CMD install)) {
            Write-Err "pnpm install 失败"
            Write-Info "可能的原因:"
            Write-Info "  1. 网络连接问题"
            Write-Info "  2. 磁盘空间不足"
            Write-Info "  3. pnpm 镜像配置问题"
            exit 1
        }
    } finally {
        Pop-Location
    }

    Write-Ok "博客依赖安装完成"
}

# =============================================================================
# 7. 构建博客
# =============================================================================

function Build-Blog {
    param([string]$BlogRoot)

    Write-Step "7. 构建博客 (pnpm build)..."

    $nodeModules = Join-Path $BlogRoot "node_modules"
    if (-not (Test-Path $nodeModules)) {
        Write-Warn "node_modules 不存在，先安装依赖..."
        Install-BlogDeps $BlogRoot
    }

    # 检查资源使用情况
    Write-Step "检查系统资源..."
    $resourceWarning = Check-ResourceWarning
    
    if ($resourceWarning) {
        Write-Warn "⚠️ 系统资源使用率较高，构建过程可能耗时较长或失败"
        Write-Warn "  当前状态可能导致内存不足或 CPU 过载"
        $continue = Read-Host "是否继续构建? (Y/N)"
        if ($continue -ne 'Y' -and $continue -ne 'y') {
            Write-Info "用户取消构建"
            exit 0
        }
    }

    # 启动资源监控
    Start-ResourceMonitor $BlogRoot

    Push-Location $BlogRoot
    try {
        Write-Info "执行: $PNPM_CMD build"
        if (-not (& $PNPM_CMD build)) {
            Stop-ResourceMonitor
            Write-Err "pnpm build 失败"
            exit 1
        }
    } finally {
        Pop-Location
    }

    $distDir = Join-Path $BlogRoot "dist"
    if (-not (Test-Path $distDir)) {
        Stop-ResourceMonitor
        Write-Err "构建完成后未找到 dist/ 目录"
        exit 1
    }

    # 停止资源监控
    Stop-ResourceMonitor
    
    Write-Ok "博客构建完成，产物位于 $distDir"
    
    # 显示构建期间的资源使用统计
    $logFile = Join-Path $BlogRoot "build_resource_monitor.log"
    if (Test-Path $logFile) {
        Write-Info "构建资源监控日志已保存到: $logFile"
    }
}

# =============================================================================
# 8. 备份旧版本
# =============================================================================

function Backup-OldVersion {
    param([string]$WebRoot)

    if ($AutoBackup -ne "true") {
        return
    }

    Write-Step "8. 备份旧版本..."

    if (-not (Test-Path $WebRoot) -or -not (Get-ChildItem $WebRoot -ErrorAction SilentlyContinue)) {
        Write-Info "部署目录为空，无需备份"
        return
    }

    $backupDir = "$WebRoot.backup.$(Get-Date -Format 'yyyyMMddHHmmss')"
    Write-Info "备份到 $backupDir"
    Copy-Item -Path $WebRoot -Destination $backupDir -Recurse -Force

    Write-Ok "备份完成"
}

# =============================================================================
# 9. 部署文件
# =============================================================================

function Deploy-Files {
    param([string]$DistDir, [string]$WebRoot)

    Write-Step "9. 部署文件..."

    Write-Info "部署: $DistDir -> $WebRoot"

    # 确保父目录存在
    $webRootParent = Split-Path $WebRoot -Parent
    if (-not (Test-Path $webRootParent)) {
        New-Item -ItemType Directory -Path $webRootParent -Force | Out-Null
    }

    # 备份旧版本
    Backup-OldVersion $WebRoot

    # 清空并复制新构建产物
    if (Test-Path $WebRoot) {
        Remove-Item "$WebRoot\*" -Recurse -Force -ErrorAction SilentlyContinue
    } else {
        New-Item -ItemType Directory -Path $WebRoot -Force | Out-Null
    }

    Copy-Item -Path "$DistDir\*" -Destination "$WebRoot\" -Recurse -Force

    Write-Ok "文件部署完成: $WebRoot"
}

# =============================================================================
# 主流程
# =============================================================================

function Main {
    Write-Host ""
    Write-Host "========================================"
    Write-Host "  AstrBot Firefly Blog Manager"
    Write-Host "  PowerShell 部署脚本 v1.0"
    Write-Host "========================================"
    Write-Host ""
    Write-Host "部署模式: $DeployMode"
    if ($DomainName) { Write-Host "域名: $DomainName" }
    if ($ServerIp) { Write-Host "远程服务器: $ServerIp" }
    Write-Host ""

    # 加载配置文件
    $configPath = if ($ConfigFile) { $ConfigFile } else { Join-Path $SCRIPT_DIR "deploy.conf" }
    Load-Config $configPath

    # 执行部署
    switch ($DeployMode) {
        "local_only" {
            Write-Step "执行本地部署..."
            Check-NodeJs
            Check-Pnpm
            Check-FireflyProject $LocalBlogRoot
            Install-BlogDeps $LocalBlogRoot
            Build-Blog $LocalBlogRoot
            Deploy-Files (Join-Path $LocalBlogRoot "dist") $WebRoot
        }

        "local_build" {
            Write-Step "执行本地构建远程部署..."
            Write-Err "Windows 脚本暂不支持 local_build 模式"
            Write-Info "请使用 deploy.sh 或在 Linux 服务器上执行"
            exit 1
        }

        "remote_build" {
            Write-Step "执行远程构建部署..."
            Write-Err "Windows 脚本暂不支持 remote_build 模式"
            Write-Info "请使用 deploy.sh 或在 Linux 服务器上执行"
            exit 1
        }

        default {
            Write-Err "未知的部署模式: $DeployMode"
            Write-Info "支持的值: local_only, local_build, remote_build"
            exit 1
        }
    }

    Write-Host ""
    Write-Host "========================================"
    Write-Ok "部署完成！"
    Write-Host "========================================"
    Write-Host ""

    if ($DomainName) {
        $protocol = if ($EnableHttps -eq "true") { "https" } else { "http" }
        Write-Host "博客地址: ${protocol}://$DomainName"
    } else {
        Write-Host "请通过服务器 IP 或配置好的域名访问博客"
    }
    Write-Host ""
}

Main
