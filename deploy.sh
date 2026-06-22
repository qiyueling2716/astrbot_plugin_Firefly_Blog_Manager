#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# AstrBot Firefly Blog Manager - 一键部署脚本
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

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_NAME="astrbot_plugin_Firefly_Blog_Manager"

# Firefly blog repository
FIREFLY_REPO="https://github.com/qiyueling2716/Firefly-Blog"

# GitHub mirror sites for faster cloning in China
declare -a GITHUB_MIRRORS=(
    "https://hubproxy.jiaozi.live/https://github.com"
    "https://ghproxy.com/https://github.com"
)

# =============================================================================
# 默认配置（可通过环境变量或 deploy.conf 覆盖）
# =============================================================================

# 部署模式: local_only | local_build | remote_build
DEPLOY_MODE="${DEPLOY_MODE:-local_only}"

# 博客目录配置
LOCAL_BLOG_ROOT="${LOCAL_BLOG_ROOT:-/var/www/firefly}"
WEB_ROOT="${WEB_ROOT:-/var/www/firefly}"

# 域名配置
DOMAIN_NAME="${DOMAIN_NAME:-}"
ENABLE_HTTPS="${ENABLE_HTTPS:-false}"
SSL_CERT_PATH="${SSL_CERT_PATH:-}"
SSL_CERT_KEY_PATH="${SSL_CERT_KEY_PATH:-}"

# Web 服务器配置: nginx | apache | none
WEB_SERVER="${WEB_SERVER:-nginx}"
NGINX_CONFIG_TEMPLATE="${NGINX_CONFIG_TEMPLATE:-}"
APACHE_VHOST_CONFIG="${APACHE_VHOST_CONFIG:-}"

# 远端服务器配置
SERVER_IP="${SERVER_IP:-}"
SERVER_PORT="${SERVER_PORT:-22}"
USERNAME="${USERNAME:-}"
AUTH_TYPE="${AUTH_TYPE:-key}"
PRIVATE_KEY_PATH="${PRIVATE_KEY_PATH:-}"
PASSWORD="${PASSWORD:-}"
REMOTE_BLOG_ROOT="${REMOTE_BLOG_ROOT:-/var/www/firefly}"
REMOTE_WEB_ROOT="${REMOTE_WEB_ROOT:-/var/www/firefly}"

# 高级选项
GITHUB_MIRROR="${GITHUB_MIRROR:-}"
AUTO_BACKUP="${AUTO_BACKUP:-true}"
BACKUP_RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-7}"

# =============================================================================
# 辅助函数
# =============================================================================

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_ok() {
    echo -e "${GREEN}[OK]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

log_step() {
    echo -e "${CYAN}[STEP]${NC} $1"
}

command_exists() {
    command -v "$1" >/dev/null 2>&1
}

version_ge() {
    # 比较版本号 $1 >= $2
    printf '%s\n%s\n' "$2" "$1" | sort -V -C
}

# =============================================================================
# 自动检测本地博客目录
# =============================================================================

find_local_blog_root() {
    # 如果已配置且存在，直接返回
    if [[ -n "${LOCAL_BLOG_ROOT:-}" && -d "$LOCAL_BLOG_ROOT" ]]; then
        echo "$LOCAL_BLOG_ROOT"
        return
    fi

    local search_paths=(
        "$(pwd)/firefly"
        "$(pwd)/blog"
        "$(pwd)"
        "/var/www/firefly"
        "/var/www/blog"
        "/root/firefly"
        "/root/blog"
        "$HOME/firefly"
        "$HOME/blog"
    )

    # 扫描常见 Web 目录下的子目录
    for base in /var/www /usr/share/nginx/html /root "$HOME"; do
        if [[ -d "$base" ]]; then
            for dir in "$base"/*/; do
                [[ -d "$dir" ]] && search_paths+=("$dir")
            done
        fi
    done

    # 检查路径是否包含 Firefly 特征
    for path in "${search_paths[@]}"; do
        if [[ -d "$path" ]]; then
            if [[ -f "$path/package.json" || -d "$path/src/content/posts" ]]; then
                log_info "自动检测到博客目录: $path"
                echo "$path"
                return
            fi
        fi
    done

    echo "/var/www/firefly"
}

# =============================================================================
# 1. 检测 Python 环境
# =============================================================================

check_python() {
    log_step "1. 检测 Python 环境..."

    if ! command_exists python3; then
        log_error "未找到 python3，请先安装 Python 3.10 或更高版本"
        log_info "安装方式:"
        log_info "  Ubuntu/Debian: sudo apt install python3 python3-pip"
        log_info "  CentOS/RHEL:   sudo yum install python3 python3-pip"
        log_info "  macOS:          brew install python3"
        exit 1
    fi

    PYTHON_VERSION=$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
    if ! version_ge "$PYTHON_VERSION" "3.10"; then
        log_error "Python 版本过低: $PYTHON_VERSION，需要 >= 3.10"
        exit 1
    fi

    log_ok "Python $PYTHON_VERSION"
}

# =============================================================================
# 2. 检测并安装 pip 依赖
# =============================================================================

check_pip_deps() {
    log_step "2. 检测 pip 依赖..."

    local req_file="$SCRIPT_DIR/requirements.txt"
    if [[ ! -f "$req_file" ]]; then
        log_warn "未找到 requirements.txt，跳过 pip 依赖检查"
        return
    fi

    local missing=()
    while IFS= read -r line || [[ -n "$line" ]]; do
        [[ -z "$line" || "$line" =~ ^# ]] && continue

        local pkg_name
        pkg_name=$(echo "$line" | sed -E 's/[<>=!~].*//')

        if ! python3 -c "import $pkg_name" 2>/dev/null; then
            if [[ "$pkg_name" == "pyyaml" ]]; then
                if ! python3 -c "import yaml" 2>/dev/null; then
                    missing+=("$line")
                fi
            else
                missing+=("$line")
            fi
        fi
    done < "$req_file"

    if [[ ${#missing[@]} -eq 0 ]]; then
        log_ok "所有 pip 依赖已安装"
        return
    fi

    log_warn "以下依赖未安装: ${missing[*]}"
    log_info "正在安装缺失的依赖..."

    if ! command_exists pip3; then
        log_error "未找到 pip3，无法安装依赖"
        exit 1
    fi

    for pkg in "${missing[@]}"; do
        log_info "安装 $pkg ..."
        if ! pip3 install "$pkg"; then
            log_error "安装 $pkg 失败"
            exit 1
        fi
    done

    log_ok "pip 依赖安装完成"
}

# =============================================================================
# 3. 检测 Node.js
# =============================================================================

check_nodejs() {
    log_step "3. 检测 Node.js 环境..."

    if ! command_exists node; then
        log_warn "未找到 Node.js，尝试自动安装..."
        install_nodejs
    fi

    NODE_VERSION=$(node --version | sed 's/v//')
    if ! version_ge "$NODE_VERSION" "22.0.0"; then
        log_error "Node.js 版本过低: $NODE_VERSION，Firefly 需要 >= 22"
        log_info "安装方式:"
        log_info "  curl -fsSL https://deb.nodesource.com/setup_22.x | sudo bash -"
        log_info "  sudo apt-get install -y nodejs"
        exit 1
    fi

    log_ok "Node.js $NODE_VERSION"
}

install_nodejs() {
    if command_exists apt; then
        log_info "使用 apt 安装 Node.js 22..."
        curl -fsSL https://deb.nodesource.com/setup_22.x | bash - || {
            log_warn "NodeSource 安装失败，尝试其他方式..."
        }
        apt-get install -y nodejs || {
            log_error "apt 安装 Node.js 失败"
            exit 1
        }
    elif command_exists yum; then
        log_info "使用 yum 安装 Node.js 22..."
        curl -fsSL https://rpm.nodesource.com/setup_22.x | bash - || {
            log_error "yum 安装 Node.js 失败"
            exit 1
        }
    elif command_exists brew; then
        log_info "使用 brew 安装 Node.js..."
        brew install node@22 || {
            log_error "brew 安装 Node.js 失败"
            exit 1
        }
    else
        log_error "无法自动安装 Node.js，请手动安装"
        exit 1
    fi

    if command_exists node; then
        log_ok "Node.js 安装完成"
    fi
}

# =============================================================================
# 4. 检测 pnpm
# =============================================================================

check_pnpm() {
    log_step "4. 检测 pnpm..."

    if ! command_exists pnpm; then
        log_warn "未找到 pnpm，尝试自动安装..."
        install_pnpm
    fi

    PNPM_VERSION=$(pnpm --version)
    log_ok "pnpm $PNPM_VERSION"
}

install_pnpm() {
    if command_exists npm; then
        log_info "使用 npm 安装 pnpm..."
        npm install -g pnpm
    elif command_exists corepack; then
        log_info "使用 corepack 安装 pnpm..."
        corepack enable
        corepack prepare pnpm@latest --activate
    else
        log_error "无法自动安装 pnpm，请先安装 Node.js 或 npm"
        exit 1
    fi

    if command_exists pnpm; then
        log_ok "pnpm 安装完成"
    fi
}

# =============================================================================
# 5. 检测 Firefly 博客项目结构
# =============================================================================

check_firefly_project() {
    log_step "5. 检测 Firefly 博客项目..."

    local blog_root="$1"

    if [[ ! -d "$blog_root" ]]; then
        log_warn "博客目录不存在: $blog_root"
        log_info "正在创建目录..."
        mkdir -p "$blog_root"
        log_ok "已创建: $blog_root"
    fi

    if [[ ! -f "$blog_root/package.json" ]]; then
        log_warn "博客目录为空，正在克隆 Firefly 仓库..."

        # 确定要使用的镜像或原始地址
        local clone_url="$FIREFLY_REPO"
        if [[ -n "$GITHUB_MIRROR" ]]; then
            clone_url="${GITHUB_MIRROR}/qiyueling2716/Firefly-Blog.git"
            log_info "使用镜像: $clone_url"
        fi

        if command_exists git; then
            local parent_dir
            parent_dir=$(dirname "$blog_root")
            mkdir -p "$parent_dir"

            # 尝试镜像
            local clone_success=false
            if [[ -n "$GITHUB_MIRROR" ]]; then
                if git clone "${GITHUB_MIRROR}/qiyueling2716/Firefly-Blog.git" "$blog_root" 2>/dev/null; then
                    clone_success=true
                fi
            fi

            # 尝试官方地址
            if [[ "$clone_success" == false ]]; then
                for mirror in "${GITHUB_MIRRORS[@]}"; do
                    log_info "尝试镜像: $mirror"
                    if git clone "${mirror}/qiyueling2716/Firefly-Blog.git" "$blog_root" 2>/dev/null; then
                        clone_success=true
                        break
                    fi
                done
            fi

            # 最后尝试官方地址
            if [[ "$clone_success" == false ]]; then
                if git clone "$FIREFLY_REPO" "$blog_root"; then
                    clone_success=true
                fi
            fi

            if [[ "$clone_success" == true ]]; then
                log_ok "Firefly 博客克隆成功"
            else
                log_error "克隆失败，请手动执行: git clone $FIREFLY_REPO $blog_root"
                exit 1
            fi
        else
            log_error "未找到 git，请安装 Git 或手动克隆"
            log_info "  apt install git"
            log_info "  git clone $FIREFLY_REPO $blog_root"
            exit 1
        fi
    fi

    if [[ ! -f "$blog_root/package.json" ]]; then
        log_error "目录 $blog_root 下未找到 package.json"
        exit 1
    fi

    log_ok "Firefly 项目验证通过: $blog_root"
}

# =============================================================================
# 资源监控函数
# =============================================================================

get_cpu_usage() {
    # 获取 CPU 使用率（百分比）
    if command_exists mpstat; then
        mpstat 1 1 | awk '/Average:/ {print 100 - $NF}'
    elif command_exists top; then
        top -bn1 | awk '/%Cpu/ {print 100 - $8}'
    elif [[ -f /proc/stat ]]; then
        # 从 /proc/stat 计算 CPU 使用率
        local cpu_line1=$(grep '^cpu ' /proc/stat)
        sleep 0.5
        local cpu_line2=$(grep '^cpu ' /proc/stat)
        
        local idle1=$(echo "$cpu_line1" | awk '{print $5}')
        local total1=$(echo "$cpu_line1" | awk '{print $2+$3+$4+$5+$6+$7+$8}')
        local idle2=$(echo "$cpu_line2" | awk '{print $5}')
        local total2=$(echo "$cpu_line2" | awk '{print $2+$3+$4+$5+$6+$7+$8}')
        
        local diff_idle=$((idle2 - idle1))
        local diff_total=$((total2 - total1))
        
        if [[ $diff_total -gt 0 ]]; then
            echo "$(( (diff_total - diff_idle) * 100 / diff_total ))"
        else
            echo "0"
        fi
    else
        echo "0"
    fi
}

get_memory_usage() {
    # 获取内存使用率（百分比）
    if command_exists free; then
        free | awk '/Mem:/ {print $3 * 100 / $2}' | awk '{printf "%.0f", $1}'
    elif [[ -f /proc/meminfo ]]; then
        local total=$(grep MemTotal /proc/meminfo | awk '{print $2}')
        local free=$(grep MemFree /proc/meminfo | awk '{print $2}')
        local buffers=$(grep Buffers /proc/meminfo | awk '{print $2}')
        local cached=$(grep ^Cached /proc/meminfo | awk '{print $2}')
        
        local used=$((total - free - buffers - cached))
        echo "$(( used * 100 / total ))"
    else
        echo "0"
    fi
}

check_resource_warning() {
    local cpu_threshold="${RESOURCE_WARNING_CPU:-80}"
    local mem_threshold="${RESOURCE_WARNING_MEM:-85}"
    
    local cpu_usage=$(get_cpu_usage)
    local mem_usage=$(get_memory_usage)
    
    log_info "当前资源使用: CPU ${cpu_usage}%, 内存 ${mem_usage}%"
    
    local has_warning=0
    
    if [[ "$cpu_usage" -ge "$cpu_threshold" ]]; then
        log_warn "⚠️ CPU 使用率 ${cpu_usage}% 超过警告阈值 ${cpu_threshold}%"
        log_warn "  建议：关闭不必要的进程或增加 CPU 资源"
        has_warning=1
    fi
    
    if [[ "$mem_usage" -ge "$mem_threshold" ]]; then
        log_warn "⚠️ 内存使用率 ${mem_usage}% 超过警告阈值 ${mem_threshold}%"
        log_warn "  建议：关闭不必要的进程或增加内存资源"
        log_warn "  构建过程可能会进一步增加内存消耗，可能导致内存不足"
        has_warning=1
    fi
    
    return $has_warning
}

monitor_resources_during_build() {
    local blog_root="$1"
    local log_file="${blog_root}/build_resource_monitor.log"
    
    # 创建监控日志文件
    echo "=== 构建资源监控日志 ===" > "$log_file"
    echo "开始时间: $(date)" >> "$log_file"
    
    # 后台监控资源使用
    (
        while true; do
            local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
            local cpu=$(get_cpu_usage)
            local mem=$(get_memory_usage)
            
            echo "[$timestamp] CPU: ${cpu}%, 内存: ${mem}%" >> "$log_file"
            
            # 检查是否超过危险阈值
            if [[ "$cpu" -ge 95 ]]; then
                echo "[$timestamp] ⚠️ CPU 使用率过高: ${cpu}%" >> "$log_file"
            fi
            if [[ "$mem" -ge 95 ]]; then
                echo "[$timestamp] ⚠️ 内存使用率过高: ${mem}%" >> "$log_file"
            fi
            
            sleep 2
        done
    ) &
    
    local monitor_pid=$!
    
    # 保存监控 PID 以便后续清理
    echo "$monitor_pid" > /tmp/firefly_build_monitor.pid
    
    return 0
}

stop_resource_monitor() {
    if [[ -f /tmp/firefly_build_monitor.pid ]]; then
        local pid=$(cat /tmp/firefly_build_monitor.pid)
        kill "$pid" 2>/dev/null || true
        rm -f /tmp/firefly_build_monitor.pid
    fi
}

# =============================================================================
# 6. 安装博客依赖
# =============================================================================

install_blog_deps() {
    log_step "6. 安装博客依赖 (pnpm install)..."

    local blog_root="$1"
    cd "$blog_root"

    # 检查资源使用情况
    check_resource_warning
    local resource_warning=$?
    
    if [[ "$resource_warning" -eq 1 ]]; then
        log_warn "资源使用较高，继续安装依赖可能会影响系统性能"
        read -p "是否继续安装? (y/N) " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            log_info "用户取消安装"
            exit 0
        fi
    fi

    if [[ -d "$blog_root/node_modules" ]]; then
        log_info "node_modules 已存在，跳过安装"
        return
    fi

    if ! pnpm install; then
        log_error "pnpm install 失败"
        log_info "可能的原因:"
        log_info "  1. 网络连接问题"
        log_info "  2. 磁盘空间不足"
        log_info "  3. pnpm 镜像配置问题"
        exit 1
    fi

    log_ok "博客依赖安装完成"
}

# =============================================================================
# 7. 构建博客
# =============================================================================

build_blog() {
    log_step "7. 构建博客 (pnpm build)..."

    local blog_root="$1"
    cd "$blog_root"

    if [[ ! -d "$blog_root/node_modules" ]]; then
        log_warn "node_modules 不存在，先安装依赖..."
        install_blog_deps "$blog_root"
    fi

    # 检查资源使用情况
    log_step "检查系统资源..."
    check_resource_warning
    local resource_warning=$?
    
    if [[ "$resource_warning" -eq 1 ]]; then
        log_warn "⚠️ 系统资源使用率较高，构建过程可能耗时较长或失败"
        log_warn "  当前状态可能导致内存不足或 CPU 过载"
        read -p "是否继续构建? (y/N) " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            log_info "用户取消构建"
            exit 0
        fi
    fi

    # 启动资源监控
    monitor_resources_during_build "$blog_root"
    trap stop_resource_monitor EXIT

    if ! pnpm build; then
        stop_resource_monitor
        log_error "pnpm build 失败"
        exit 1
    fi

    if [[ ! -d "$blog_root/dist" ]]; then
        stop_resource_monitor
        log_error "构建完成后未找到 dist/ 目录"
        exit 1
    fi

    # 停止资源监控
    stop_resource_monitor
    
    log_ok "博客构建完成，产物位于 $blog_root/dist"
    
    # 显示构建期间的资源使用统计
    if [[ -f "${blog_root}/build_resource_monitor.log" ]]; then
        log_info "构建资源监控日志已保存到: ${blog_root}/build_resource_monitor.log"
    fi
}

# =============================================================================
# 8. 备份旧版本
# =============================================================================

backup_old_version() {
    if [[ "$AUTO_BACKUP" != "true" ]]; then
        return
    fi

    local web_root="$1"
    log_step "8. 备份旧版本..."

    if [[ ! -d "$web_root" ]] || [[ -z "$(ls -A "$web_root" 2>/dev/null)" ]]; then
        log_info "部署目录为空，无需备份"
        return
    fi

    local backup_dir="${web_root}.backup.$(date +%Y%m%d%H%M%S)"
    log_info "备份到 $backup_dir"
    cp -r "$web_root" "$backup_dir"

    # 清理旧备份
    if [[ -d "${web_root}.backup."* ]]; then
        local cutoff_date
        cutoff_date=$(date -d "$BACKUP_RETENTION_DAYS days ago" +%Y%m%d%H%M%S 2>/dev/null || date -v-${BACKUP_RETENTION_DAYS}d +%Y%m%d%H%M%S)
        for old_backup in "${web_root}.backup."*; do
            [[ -d "$old_backup" ]] || continue
            local backup_date
            backup_date=$(basename "$old_backup" | sed 's/.*\.backup\.\([0-9]*\).*/\1/')
            if [[ "$backup_date" < "$cutoff_date" ]]; then
                log_info "删除旧备份: $old_backup"
                rm -rf "$old_backup"
            fi
        done
    fi

    log_ok "备份完成"
}

# =============================================================================
# 9. 部署文件
# =============================================================================

deploy_files() {
    local dist_dir="$1"
    local web_root="$2"
    log_step "9. 部署文件..."

    log_info "部署: $dist_dir -> $web_root"

    # 确保父目录存在
    local web_root_parent
    web_root_parent=$(dirname "$web_root")
    if [[ ! -d "$web_root_parent" ]]; then
        mkdir -p "$web_root_parent"
    fi

    # 备份旧版本
    backup_old_version "$web_root"

    # 清空并复制新构建产物
    if [[ -d "$web_root" ]]; then
        rm -rf "$web_root"/*
    else
        mkdir -p "$web_root"
    fi
    cp -r "$dist_dir"/* "$web_root/"

    log_ok "文件部署完成: $web_root"
}

# =============================================================================
# 10. 配置 Web 服务器
# =============================================================================

configure_web_server() {
    local web_root="$1"
    local domain="$2"
    local enable_https="$3"
    local ssl_cert="$4"
    local ssl_cert_key="$5"
    local server_type="$6"

    log_step "10. 配置 Web 服务器..."

    if [[ "$server_type" == "none" ]]; then
        log_info "跳过 Web 服务器配置（WEB_SERVER=none）"
        return
    fi

    if [[ "$server_type" == "nginx" ]]; then
        configure_nginx "$web_root" "$domain" "$enable_https" "$ssl_cert" "$ssl_cert_key"
    elif [[ "$server_type" == "apache" ]]; then
        configure_apache "$web_root" "$domain" "$enable_https" "$ssl_cert" "$ssl_cert_key"
    else
        log_warn "未知的 Web 服务器类型: $server_type，跳过配置"
    fi
}

configure_nginx() {
    local web_root="$1"
    local domain="$2"
    local enable_https="$3"
    local ssl_cert="$4"
    local ssl_cert_key="$5"

    log_info "配置 Nginx..."

    if ! command_exists nginx; then
        log_warn "Nginx 未安装，跳过配置"
        log_info "安装方式: sudo apt install nginx"
        return
    fi

    # 确定配置文件名
    local config_name="${domain:-firefly}"
    local config_file="/etc/nginx/sites-available/${config_name}"
    local config_link="/etc/nginx/sites-enabled/${config_name}"

    # 如果使用自定义模板
    if [[ -n "$NGINX_CONFIG_TEMPLATE" && -f "$NGINX_CONFIG_TEMPLATE" ]]; then
        log_info "使用自定义配置模板: $NGINX_CONFIG_TEMPLATE"
        cp "$NGINX_CONFIG_TEMPLATE" "$config_file"
    else
        # 生成 Nginx 配置
        cat > "$config_file" << EOF
# Firefly Blog Nginx Configuration
# 由 AstrBot Firefly Blog Manager 自动生成
# 生成时间: $(date '+%Y-%m-%d %H:%M:%S')

server {
    listen 80;
    listen [::]:80;

    # 如果配置了域名
    $([[ -n "$domain" ]] && echo "server_name $domain;" || echo "# server_name $domain;")

    root $web_root;
    index index.html;

    # 访问日志
    access_log /var/log/nginx/${config_name}_access.log;
    error_log /var/log/nginx/${config_name}_error.log;

    # 静态文件缓存
    location ~* \.(js|css|png|jpg|jpeg|gif|ico|svg|woff|woff2|ttf|eot)$ {
        expires 30d;
        add_header Cache-Control "public, immutable";
    }

    # SPA 路由支持
    location / {
        try_files \$uri \$uri/ /index.html;
    }

    # 安全 headers
    location ~ /\. {
        deny all;
    }

$(if [[ "$enable_https" == "true" ]]; then
cat << HTTPS_BLOCK
    # HTTPS 配置
    listen 443 ssl http2;
    listen [::]:443 ssl http2;

    ssl_certificate $ssl_cert;
    ssl_certificate_key $ssl_cert_key;

    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;
HTTPS_BLOCK
fi)
}

$(if [[ "$enable_https" == "true" && -n "$domain" ]]; then
cat << HTTP_REDIRECT
# HTTP 重定向到 HTTPS
server {
    listen 80;
    listen [::]:80;
    server_name $domain;
    return 301 https://\$server_name\$request_uri;
}
HTTP_REDIRECT
fi)
EOF
    fi

    # 启用配置
    if [[ ! -L "$config_link" ]]; then
        ln -sf "$config_file" "$config_link"
    fi

    # 测试并重载 Nginx
    if nginx -t; then
        systemctl reload nginx 2>/dev/null || systemctl restart nginx 2>/dev/null || {
            log_warn "无法重载 Nginx，请手动执行: sudo systemctl reload nginx"
        }
        log_ok "Nginx 配置完成"
    else
        log_error "Nginx 配置测试失败"
        exit 1
    fi
}

configure_apache() {
    local web_root="$1"
    local domain="$2"
    local enable_https="$3"
    local ssl_cert="$4"
    local ssl_cert_key="$5"

    log_info "配置 Apache..."

    if ! command_exists apache2; then
        log_warn "Apache 未安装，跳过配置"
        log_info "安装方式: sudo apt install apache2"
        return
    fi

    # 确定配置文件名
    local config_name="${domain:-firefly}"
    local config_file="/etc/apache2/sites-available/${config_name}.conf"
    local config_link="/etc/apache2/sites-enabled/${config_name}.conf"

    # 如果使用自定义配置
    if [[ -n "$APACHE_VHOST_CONFIG" && -f "$APACHE_VHOST_CONFIG" ]]; then
        log_info "使用自定义配置: $APACHE_VHOST_CONFIG"
        cp "$APACHE_VHOST_CONFIG" "$config_file"
    else
        # 生成 Apache 配置
        cat > "$config_file" << EOF
# Firefly Blog Apache Configuration
# 由 AstrBot Firefly Blog Manager 自动生成
# 生成时间: $(date '+%Y-%m-%d %H:%M:%S')

<VirtualHost *:80>
    ServerName ${domain:-localhost}
    DocumentRoot $web_root

    <Directory "$web_root">
        Options Indexes FollowSymLinks
        AllowOverride All
        Require all granted
    </Directory>

    # 静态文件缓存
    <FilesMatch "\.(js|css|png|jpg|jpeg|gif|ico|svg|woff|woff2|ttf|eot)$">
        Header set Cache-Control "public, max-age=2592000"
    </FilesMatch>

    ErrorLog \${APACHE_LOG_DIR}/${config_name}_error.log
    CustomLog \${APACHE_LOG_DIR}/${config_name}_access.log combined
</VirtualHost>

$(if [[ "$enable_https" == "true" ]]; then
cat << HTTPS_BLOCK
<VirtualHost *:443>
    ServerName ${domain:-localhost}
    DocumentRoot $web_root

    SSLEngine on
    SSLCertificateFile $ssl_cert
    SSLCertificateKeyFile $ssl_cert_key

    <Directory "$web_root">
        Options Indexes FollowSymLinks
        AllowOverride All
        Require all granted
    </Directory>

    ErrorLog \${APACHE_LOG_DIR}/${config_name}_ssl_error.log
    CustomLog \${APACHE_LOG_DIR}/${config_name}_ssl_access.log combined
</VirtualHost>

# HTTP 重定向到 HTTPS
<VirtualHost *:80>
    ServerName ${domain:-localhost}
    Redirect permanent / https://\${HTTP_HOST}/
</VirtualHost>
HTTPS_BLOCK
fi)
EOF
    fi

    # 启用配置
    if [[ ! -L "$config_link" ]]; then
        ln -sf "$config_file" "$config_link"
    fi

    # 启用必要模块
    a2enmod ssl rewrite headers 2>/dev/null || true

    # 测试并重载 Apache
    if apache2ctl configtest 2>/dev/null; then
        systemctl reload apache2 2>/dev/null || systemctl restart apache2 2>/dev/null || {
            log_warn "无法重载 Apache，请手动执行: sudo systemctl reload apache2"
        }
        log_ok "Apache 配置完成"
    else
        log_error "Apache 配置测试失败"
        exit 1
    fi
}

# =============================================================================
# 验证 SSL 证书
# =============================================================================

validate_ssl_cert() {
    local cert_path="$1"
    local key_path="$2"

    if [[ ! -f "$cert_path" ]]; then
        log_error "SSL 证书文件不存在: $cert_path"
        return 1
    fi

    if [[ ! -f "$key_path" ]]; then
        log_error "SSL 证书密钥文件不存在: $key_path"
        return 1
    fi

    # 验证证书格式
    if command_exists openssl; then
        if ! openssl x509 -noout -modulus -in "$cert_path" >/dev/null 2>&1; then
            log_error "SSL 证书格式无效: $cert_path"
            return 1
        fi

        if ! openssl rsa -noout -modulus -in "$key_path" >/dev/null 2>&1; then
            log_error "SSL 证书密钥格式无效: $key_path"
            return 1
        fi

        local cert_md5
        local key_md5
        cert_md5=$(openssl x509 -noout -modulus -in "$cert_path" | md5sum | cut -d' ' -f1)
        key_md5=$(openssl rsa -noout -modulus -in "$key_path" | md5sum | cut -d' ' -f1)

        if [[ "$cert_md5" != "$key_md5" ]]; then
            log_error "SSL 证书和密钥不匹配"
            return 1
        fi

        log_ok "SSL 证书验证通过"
    else
        log_warn "openssl 未安装，无法验证证书匹配性"
    fi

    return 0
}

# =============================================================================
# 本地部署
# =============================================================================

deploy_local() {
    local blog_root="$LOCAL_BLOG_ROOT"
    local web_root="$WEB_ROOT"

    log_step "执行本地部署..."

    # 检查依赖
    check_firefly_project "$blog_root"
    install_blog_deps "$blog_root"
    build_blog "$blog_root"

    # 部署文件
    deploy_files "$blog_root/dist" "$web_root"

    # 配置 Web 服务器
    if [[ -n "$DOMAIN_NAME" || "$WEB_SERVER" != "none" ]]; then
        if [[ "$ENABLE_HTTPS" == "true" ]]; then
            if ! validate_ssl_cert "$SSL_CERT_PATH" "$SSL_CERT_KEY_PATH"; then
                log_warn "SSL 证书验证失败，跳过 HTTPS 配置"
                ENABLE_HTTPS="false"
            fi
        fi
        configure_web_server "$web_root" "$DOMAIN_NAME" "$ENABLE_HTTPS" "$SSL_CERT_PATH" "$SSL_CERT_KEY_PATH" "$WEB_SERVER"
    fi
}

# =============================================================================
# 本地构建远程部署
# =============================================================================

deploy_local_to_remote() {
    local blog_root="$LOCAL_BLOG_ROOT"
    local web_root="$WEB_ROOT"

    log_step "执行本地构建远程部署..."

    if [[ -z "$SERVER_IP" || -z "$USERNAME" ]]; then
        log_error "远程部署需要配置 SERVER_IP 和 USERNAME"
        exit 1
    fi

    # 本地构建
    check_firefly_project "$blog_root"
    install_blog_deps "$blog_root"
    build_blog "$blog_root"

    # 通过 SSH 部署
    log_step "部署到远程服务器..."

    local dist_dir="$blog_root/dist"
    if [[ ! -d "$dist_dir" ]]; then
        log_error "构建产物不存在: $dist_dir"
        exit 1
    fi

    local ssh_opts="-p $SERVER_PORT -o StrictHostKeyChecking=no -o ConnectTimeout=10"
    if [[ "$AUTH_TYPE" == "key" && -n "$PRIVATE_KEY_PATH" && -f "$PRIVATE_KEY_PATH" ]]; then
        ssh_opts="$ssh_opts -i $PRIVATE_KEY_PATH"
    fi

    # 在远程服务器上执行部署
    local remote_cmds="
        set -e
        # 创建部署目录
        mkdir -p $web_root

        # 备份旧版本
        $([[ "$AUTO_BACKUP" == "true" ]] && echo "if [ -d \"$web_root\" ] && [ \"\$(ls -A $web_root 2>/dev/null)\" ]; then cp -r $web_root ${web_root}.backup.\$(date +%Y%m%d%H%M%S); fi")
    "

    # 执行远程命令
    if [[ "$AUTH_TYPE" == "password" && -n "$PASSWORD" ]]; then
        if ! command_exists sshpass; then
            log_error "密码认证需要 sshpass，请先安装: sudo apt install sshpass"
            exit 1
        fi
        sshpass -p "$PASSWORD" ssh $ssh_opts "$USERNAME@$SERVER_IP" "$remote_cmds"
    else
        ssh $ssh_opts "$USERNAME@$SERVER_IP" "$remote_cmds"
    fi

    # 优先使用 rsync
    if command_exists rsync; then
        log_info "使用 rsync 同步文件..."
        local rsync_opts="-avz --delete"

        if [[ "$AUTH_TYPE" == "password" && -n "$PASSWORD" ]]; then
            if command_exists sshpass; then
                rsync_opts="$rsync_opts -e 'sshpass -p $PASSWORD ssh $ssh_opts'"
                rsync $rsync_opts "$dist_dir/" "$USERNAME@$SERVER_IP:$web_root/" || {
                    log_warn "rsync 失败，尝试 scp..."
                    deploy_via_scp "$dist_dir" "$SERVER_IP" "$USERNAME" "$SERVER_PORT" "$AUTH_TYPE" "$web_root"
                }
            fi
        else
            rsync $rsync_opts -e "ssh $ssh_opts" "$dist_dir/" "$USERNAME@$SERVER_IP:$web_root/" || {
                log_warn "rsync 失败，尝试 scp..."
                deploy_via_scp "$dist_dir" "$SERVER_IP" "$USERNAME" "$SERVER_PORT" "$AUTH_TYPE" "$web_root"
            }
        fi
    else
        deploy_via_scp "$dist_dir" "$SERVER_IP" "$USERNAME" "$SERVER_PORT" "$AUTH_TYPE" "$web_root"
    fi

    log_ok "文件同步完成"

    # 在远程服务器上配置 Web 服务器
    if [[ -n "$DOMAIN_NAME" || "$WEB_SERVER" != "none" ]]; then
        log_step "在远程服务器上配置 Web 服务器..."
        if [[ "$ENABLE_HTTPS" == "true" ]]; then
            if ! validate_ssl_cert "$SSL_CERT_PATH" "$SSL_CERT_KEY_PATH"; then
                log_warn "SSL 证书验证失败，跳过 HTTPS 配置"
                ENABLE_HTTPS="false"
            fi
        fi
        configure_web_server_remote "$web_root" "$DOMAIN_NAME" "$ENABLE_HTTPS" "$SSL_CERT_PATH" "$SSL_CERT_KEY_PATH" "$WEB_SERVER"
    fi
}

deploy_via_scp() {
    local dist_dir="$1"
    local hostname="$2"
    local username="$3"
    local port="$4"
    local auth_type="$5"
    local web_root="$6"

    log_info "使用 scp 同步文件..."

    local ssh_opts="-P $port -o StrictHostKeyChecking=no"
    if [[ "$auth_type" == "key" && -n "$PRIVATE_KEY_PATH" && -f "$PRIVATE_KEY_PATH" ]]; then
        ssh_opts="$ssh_opts -i $PRIVATE_KEY_PATH"
    fi

    if [[ "$auth_type" == "password" && -n "$PASSWORD" ]]; then
        if ! command_exists sshpass; then
            log_error "密码认证需要 sshpass"
            exit 1
        fi
        sshpass -p "$PASSWORD" scp -r $ssh_opts "$dist_dir"/* "$username@$hostname:$web_root/"
    else
        scp -r $ssh_opts "$dist_dir"/* "$username@$hostname:$web_root/"
    fi
}

configure_web_server_remote() {
    local web_root="$1"
    local domain="$2"
    local enable_https="$3"
    local ssl_cert="$4"
    local ssl_cert_key="$5"
    local server_type="$6"

    if [[ "$server_type" == "none" ]]; then
        return
    fi

    local remote_cmds=""
    if [[ "$server_type" == "nginx" ]]; then
        remote_cmds=$(generate_nginx_remote_config "$web_root" "$domain" "$enable_https" "$ssl_cert" "$ssl_cert_key")
    elif [[ "$server_type" == "apache" ]]; then
        remote_cmds=$(generate_apache_remote_config "$web_root" "$domain" "$enable_https" "$ssl_cert" "$ssl_cert_key")
    fi

    if [[ -n "$remote_cmds" ]]; then
        if [[ "$AUTH_TYPE" == "password" && -n "$PASSWORD" ]]; then
            sshpass -p "$PASSWORD" ssh -p "$SERVER_PORT" -o StrictHostKeyChecking=no "$USERNAME@$SERVER_IP" "$remote_cmds"
        else
            ssh -p "$SERVER_PORT" -o StrictHostKeyChecking=no -i "$PRIVATE_KEY_PATH" "$USERNAME@$SERVER_IP" "$remote_cmds"
        fi
    fi
}

generate_nginx_remote_config() {
    cat << 'NginxEOF'
# 配置 Nginx 的远程命令
if command -v nginx >/dev/null 2>&1; then
    echo "Nginx 已安装，配置步骤请在服务器上手动执行"
else
    echo "Nginx 未安装，请先安装: sudo apt install nginx"
fi
NginxEOF
}

generate_apache_remote_config() {
    cat << 'ApacheEOF'
# 配置 Apache 的远程命令
if command -v apache2 >/dev/null 2>&1; then
    echo "Apache 已安装，配置步骤请在服务器上手动执行"
else
    echo "Apache 未安装，请先安装: sudo apt install apache2"
fi
ApacheEOF
}

# =============================================================================
# 远程构建部署
# =============================================================================

deploy_remote() {
    log_step "执行远程构建部署..."

    if [[ -z "$SERVER_IP" || -z "$USERNAME" ]]; then
        log_error "远程部署需要配置 SERVER_IP 和 USERNAME"
        exit 1
    fi

    local ssh_opts="-p $SERVER_PORT -o StrictHostKeyChecking=no -o ConnectTimeout=10"
    if [[ "$AUTH_TYPE" == "key" && -n "$PRIVATE_KEY_PATH" && -f "$PRIVATE_KEY_PATH" ]]; then
        ssh_opts="$ssh_opts -i $PRIVATE_KEY_PATH"
    fi

    local remote_cmds="
        set -e
        cd $REMOTE_BLOG_ROOT

        # 克隆仓库（如果不存在）
        if [ ! -f package.json ]; then
            echo 'Cloning Firefly repository...'
            $([[ -n "$GITHUB_MIRROR" ]] && echo "git clone ${GITHUB_MIRROR}/qiyueling2716/Firefly-Blog.git ." || echo "git clone $FIREFLY_REPO .")
        fi

        # 安装依赖
        if [ ! -d node_modules ]; then
            echo 'Installing dependencies...'
            pnpm install
        fi

        # 构建
        echo 'Building...'
        pnpm build

        # 部署文件
        echo 'Deploying...'
        mkdir -p $REMOTE_WEB_ROOT
        $([[ "$AUTO_BACKUP" == "true" ]] && echo "if [ -d \"$REMOTE_WEB_ROOT\" ] && [ \"\$(ls -A $REMOTE_WEB_ROOT 2>/dev/null)\" ]; then cp -r $REMOTE_WEB_ROOT ${REMOTE_WEB_ROOT}.backup.\$(date +%Y%m%d%H%M%S); fi")
        rm -rf $REMOTE_WEB_ROOT/*
        cp -r $REMOTE_BLOG_ROOT/dist/* $REMOTE_WEB_ROOT/

        echo 'Done'
    "

    log_info "在远程服务器 $SERVER_IP 执行构建..."

    if [[ "$AUTH_TYPE" == "password" && -n "$PASSWORD" ]]; then
        if ! command_exists sshpass; then
            log_error "密码认证需要 sshpass"
            exit 1
        fi
        sshpass -p "$PASSWORD" ssh $ssh_opts "$USERNAME@$SERVER_IP" "$remote_cmds"
    else
        ssh $ssh_opts "$USERNAME@$SERVER_IP" "$remote_cmds"
    fi

    log_ok "远程构建部署完成"

    # 配置远程 Web 服务器
    if [[ -n "$DOMAIN_NAME" || "$WEB_SERVER" != "none" ]]; then
        log_step "在远程服务器上配置 Web 服务器..."
        configure_web_server_remote "$REMOTE_WEB_ROOT" "$DOMAIN_NAME" "$ENABLE_HTTPS" "$SSL_CERT_PATH" "$SSL_CERT_KEY_PATH" "$WEB_SERVER"
    fi
}

# =============================================================================
# 加载配置文件
# =============================================================================

load_config() {
    local config_file="${SCRIPT_DIR}/deploy.conf"
    if [[ -f "$config_file" ]]; then
        log_info "加载配置文件: $config_file"
        set -a
        source "$config_file"
        set +a
    fi
}

# =============================================================================
# 主流程
# =============================================================================

main() {
    echo ""
    echo "========================================"
    echo "  AstrBot Firefly Blog Manager"
    echo "  一键部署脚本 v1.0"
    echo "========================================"
    echo ""
    echo "部署模式: $DEPLOY_MODE"
    [[ -n "$DOMAIN_NAME" ]] && echo "域名: $DOMAIN_NAME"
    [[ -n "$SERVER_IP" ]] && echo "远程服务器: $SERVER_IP"
    echo ""

    # 加载配置
    load_config

    # 自动检测博客目录
    if [[ "$DEPLOY_MODE" == "local_only" || "$DEPLOY_MODE" == "local_build" ]]; then
        LOCAL_BLOG_ROOT=$(find_local_blog_root)
        echo "博客目录: $LOCAL_BLOG_ROOT"
        echo ""
    fi

    # 执行部署
    case "$DEPLOY_MODE" in
        local_only)
            deploy_local
            ;;

        local_build)
            deploy_local_to_remote
            ;;

        remote_build)
            deploy_remote
            ;;

        *)
            log_error "未知的部署模式: $DEPLOY_MODE"
            log_info "支持的值: local_only, local_build, remote_build"
            exit 1
            ;;
    esac

    echo ""
    echo "========================================"
    log_ok "部署完成！"
    echo "========================================"
    echo ""

    if [[ -n "$DOMAIN_NAME" ]]; then
        echo "博客地址: $([[ "$ENABLE_HTTPS" == "true" ]] && echo "https" || echo "http")://$DOMAIN_NAME"
    else
        echo "请通过服务器 IP 或配置好的域名访问博客"
    fi
    echo ""
}

main "$@"
