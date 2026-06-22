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
# =============================================================================

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_NAME="astrbot_plugin_Firefly_Blog_Manager"

# Firefly blog repository (from README)
FIREFLY_REPO="https://github.com/qiyueling2716/Firefly-Blog"

# GitHub mirror sites for faster cloning in China
GITHUB_MIRRORS=(
    "https://hubproxy.jiaozi.live/https://github.com/qiyueling2716/Firefly-Blog.git"
)

# 默认配置（可通过环境变量覆盖）
DEPLOY_MODE="${DEPLOY_MODE:-local_only}"
LOCAL_BLOG_ROOT="${LOCAL_BLOG_ROOT:-}"
WEB_ROOT="${WEB_ROOT:-/var/www/html}"
SERVER_IP="${SERVER_IP:-}"
SERVER_PORT="${SERVER_PORT:-22}"
USERNAME="${USERNAME:-}"
AUTH_TYPE="${AUTH_TYPE:-key}"
PRIVATE_KEY_PATH="${PRIVATE_KEY_PATH:-}"
PASSWORD="${PASSWORD:-}"
REMOTE_BLOG_ROOT="${REMOTE_BLOG_ROOT:-/var/www/firefly}"
REMOTE_WEB_ROOT="${REMOTE_WEB_ROOT:-/var/www/html}"

# -----------------------------------------------------------------------------
# 辅助函数
# -----------------------------------------------------------------------------

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_ok() {
    echo -e "${GREEN}[OK]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

# -----------------------------------------------------------------------------
# 自动检测本地博客目录
# -----------------------------------------------------------------------------

find_local_blog_root() {
    # 如果已配置且存在，直接返回
    if [[ -n "${LOCAL_BLOG_ROOT:-}" && -d "$LOCAL_BLOG_ROOT" ]]; then
        echo "$LOCAL_BLOG_ROOT"
        return
    fi

    local search_paths=(
        # 当前目录及子目录
        "$(pwd)/firefly"
        "$(pwd)/blog"
        "$(pwd)"
        # 常见 Linux 路径
        "/var/www/firefly"
        "/var/www/blog"
        "/var/www/html/firefly"
        "/usr/share/nginx/html/firefly"
        "/usr/share/nginx/html/blog"
        # 用户目录
        "$HOME/firefly"
        "$HOME/blog"
        "$HOME/projects/firefly"
    )

    # 扫描常见 Web 目录下的子目录
    for base in /var/www /usr/share/nginx/html; do
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

    # 未找到，返回默认值
    echo "/var/www/firefly"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

command_exists() {
    command -v "$1" >/dev/null 2>&1
}

version_ge() {
    # 比较版本号 $1 >= $2
    printf '%s\n%s\n' "$2" "$1" | sort -V -C
}

# -----------------------------------------------------------------------------
# 1. 检测 Python 环境
# -----------------------------------------------------------------------------

check_python() {
    log_info "检测 Python 环境..."

    if ! command_exists python3; then
        log_error "未找到 python3，请先安装 Python 3.10 或更高版本"
        exit 1
    fi

    PYTHON_VERSION=$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
    if ! version_ge "$PYTHON_VERSION" "3.10"; then
        log_error "Python 版本过低: $PYTHON_VERSION，需要 >= 3.10"
        exit 1
    fi

    log_ok "Python $PYTHON_VERSION"
}

# -----------------------------------------------------------------------------
# 2. 检测并安装 pip 依赖
# -----------------------------------------------------------------------------

check_pip_deps() {
    log_info "检测 pip 依赖..."

    local req_file="$SCRIPT_DIR/requirements.txt"
    if [[ ! -f "$req_file" ]]; then
        log_warn "未找到 requirements.txt，跳过 pip 依赖检查"
        return
    fi

    local missing=()
    while IFS= read -r line || [[ -n "$line" ]]; do
        # 跳过空行和注释
        [[ -z "$line" || "$line" =~ ^# ]] && continue

        # 提取包名（去掉版本号）
        local pkg_name
        pkg_name=$(echo "$line" | sed -E 's/[<>=!~].*//')

        if ! python3 -c "import $pkg_name" 2>/dev/null; then
            # 特殊处理：pyyaml 的 import 名是 yaml
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

# -----------------------------------------------------------------------------
# 3. 检测 Node.js
# -----------------------------------------------------------------------------

check_nodejs() {
    log_info "检测 Node.js 环境..."

    if ! command_exists node; then
        log_warn "未找到 Node.js，尝试自动安装..."
        install_nodejs
    fi

    NODE_VERSION=$(node --version | sed 's/v//')
    if ! version_ge "$NODE_VERSION" "22.0.0"; then
        log_error "Node.js 版本过低: $NODE_VERSION，Firefly 需要 >= 22"
        exit 1
    fi

    log_ok "Node.js $NODE_VERSION"
}

install_nodejs() {
    # 尝试使用 nvm
    if command_exists nvm; then
        log_info "使用 nvm 安装 Node.js 22..."
        nvm install 22
        nvm use 22
        if command_exists node; then
            log_ok "Node.js 安装完成"
            return
        fi
        log_warn "nvm 安装失败，尝试其他方式..."
    fi

    # 尝试使用包管理器
    if command_exists apt; then
        log_info "使用 apt 安装 Node.js..."
        curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
        apt-get install -y nodejs
        if command_exists node; then
            log_ok "Node.js 安装完成"
            return
        fi
    elif command_exists yum; then
        log_info "使用 yum 安装 Node.js..."
        curl -fsSL https://rpm.nodesource.com/setup_22.x | bash -
        yum install -y nodejs
        if command_exists node; then
            log_ok "Node.js 安装完成"
            return
        fi
    elif command_exists brew; then
        log_info "使用 brew 安装 Node.js..."
        brew install node@22
        if command_exists node; then
            log_ok "Node.js 安装完成"
            return
        fi
    fi

    log_error "无法自动安装 Node.js，请手动安装"
    log_info "安装方式:"
    log_info "  - 使用 nvm: curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.0/install.sh | bash"
    log_info "  - 或访问 https://nodejs.org/ 下载安装"
    exit 1
}

# -----------------------------------------------------------------------------
# 4. 检测 pnpm
# -----------------------------------------------------------------------------

check_pnpm() {
    log_info "检测 pnpm..."

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
        if command_exists pnpm; then
            log_ok "pnpm 安装完成"
            return
        fi
        log_warn "npm 安装失败，尝试 corepack..."
    fi

    if command_exists corepack; then
        log_info "使用 corepack 安装 pnpm..."
        corepack enable
        corepack prepare pnpm@latest --activate
        if command_exists pnpm; then
            log_ok "pnpm 安装完成"
            return
        fi
    fi

    log_error "无法自动安装 pnpm，请手动安装"
    log_info "安装方式: npm install -g pnpm"
    log_info "  或: corepack enable && corepack prepare pnpm@latest --activate"
    exit 1
}

# -----------------------------------------------------------------------------
# 5. 检测 Firefly 博客项目结构
# -----------------------------------------------------------------------------

check_firefly_project() {
    log_info "检测 Firefly 博客项目..."

    local blog_root="$1"

    if [[ ! -d "$blog_root" ]]; then
        log_warn "博客目录不存在: $blog_root"
        log_info "正在创建目录..."
        mkdir -p "$blog_root"
        log_ok "已创建: $blog_root"
    fi

    # Check if directory is empty (no package.json)
    if [[ ! -f "$blog_root/package.json" ]]; then
        log_warn "博客目录为空，正在克隆 Firefly 仓库..."
        if command_exists git; then
            local parent_dir
            parent_dir=$(dirname "$blog_root")
            local repo_name
            repo_name=$(basename "$FIREFLY_REPO")
            local clone_success=false

            cd "$parent_dir"

            # Try mirror sites first for faster cloning
            for mirror_url in "${GITHUB_MIRRORS[@]}"; do
                log_info "尝试镜像: $mirror_url"
                if git clone "$mirror_url" 2>/dev/null; then
                    if [[ -d "$parent_dir/$repo_name" ]]; then
                        mv "$parent_dir/$repo_name"/* "$blog_root/"
                        mv "$parent_dir/$repo_name"/.[^.]* "$blog_root/" 2>/dev/null || true
                        rm -rf "$parent_dir/$repo_name"
                        log_ok "Firefly 博客已通过镜像 $mirror 克隆"
                        clone_success=true
                        break
                    fi
                fi
                # Clean up failed clone
                rm -rf "$parent_dir/$repo_name" 2>/dev/null || true
            done

            # Fallback to original GitHub
            if [[ "$clone_success" == false ]]; then
                log_info "尝试原始地址: $FIREFLY_REPO"
                if git clone "$FIREFLY_REPO"; then
                    if [[ -d "$parent_dir/$repo_name" ]]; then
                        mv "$parent_dir/$repo_name"/* "$blog_root/"
                        mv "$parent_dir/$repo_name"/.[^.]* "$blog_root/" 2>/dev/null || true
                        rm -rf "$parent_dir/$repo_name"
                        log_ok "Firefly 博客已从 GitHub 克隆"
                        clone_success=true
                    fi
                fi
            fi

            if [[ "$clone_success" == false ]]; then
                log_warn "所有克隆尝试均失败"
                log_warn "请手动克隆: git clone $FIREFLY_REPO $blog_root"
            fi
        else
            log_warn "未找到 git，请安装 Git 或手动克隆"
            log_warn "  git clone $FIREFLY_REPO $blog_root"
        fi
    fi

    # Final check after clone attempt
    if [[ ! -f "$blog_root/package.json" ]]; then
        log_error "目录 $blog_root 下未找到 package.json，这不是一个有效的 Firefly 项目"
        exit 1
    fi

    if [[ ! -d "$blog_root/src/content/posts" ]]; then
        log_warn "未找到 src/content/posts 目录，请确认这是 Firefly 博客项目"
    fi

    log_ok "Firefly 项目验证通过: $blog_root"
}

# -----------------------------------------------------------------------------
# 6. 安装博客依赖
# -----------------------------------------------------------------------------

install_blog_deps() {
    local blog_root="$1"
    log_info "安装博客依赖 (pnpm install)..."

    cd "$blog_root"

    if [[ -d "$blog_root/node_modules" ]]; then
        log_warn "node_modules 已存在，跳过安装"
        return
    fi

    if ! pnpm install; then
        log_error "pnpm install 失败"
        exit 1
    fi

    log_ok "博客依赖安装完成"
}

# -----------------------------------------------------------------------------
# 7. 构建博客
# -----------------------------------------------------------------------------

build_blog() {
    local blog_root="$1"
    log_info "构建博客 (pnpm build)..."

    cd "$blog_root"

    if [[ ! -d "$blog_root/node_modules" ]]; then
        log_warn "node_modules 不存在，先安装依赖..."
        install_blog_deps "$blog_root"
    fi

    if ! pnpm build; then
        log_error "pnpm build 失败"
        exit 1
    fi

    if [[ ! -d "$blog_root/dist" ]]; then
        log_error "构建完成后未找到 dist/ 目录"
        exit 1
    fi

    log_ok "博客构建完成，产物位于 $blog_root/dist"
}

# -----------------------------------------------------------------------------
# 8. 部署
# -----------------------------------------------------------------------------

deploy_local() {
    local dist_dir="$LOCAL_BLOG_ROOT/dist"
    log_info "本地部署: $dist_dir -> $WEB_ROOT"

    if [[ ! -d "$dist_dir" ]]; then
        log_error "构建产物不存在: $dist_dir"
        exit 1
    fi

    # 确保 web root 父目录存在
    local web_root_parent
    web_root_parent=$(dirname "$WEB_ROOT")
    if [[ ! -d "$web_root_parent" ]]; then
        log_info "创建 web root 父目录: $web_root_parent"
        mkdir -p "$web_root_parent"
    fi

    # 备份旧版本
    if [[ -d "$WEB_ROOT" && "$(ls -A "$WEB_ROOT" 2>/dev/null)" ]]; then
        local backup_dir="${WEB_ROOT}.backup.$(date +%Y%m%d%H%M%S)"
        log_info "备份旧版本到 $backup_dir"
        cp -r "$WEB_ROOT" "$backup_dir"
    fi

    # 清空并复制新构建产物
    rm -rf "$WEB_ROOT"
    cp -r "$dist_dir" "$WEB_ROOT"

    log_ok "已部署到 $WEB_ROOT"
}

deploy_local_to_remote() {
    local dist_dir="$LOCAL_BLOG_ROOT/dist"
    log_info "本地构建 -> 远程部署: $dist_dir -> $SERVER_IP:$REMOTE_WEB_ROOT"

    if [[ ! -d "$dist_dir" ]]; then
        log_error "构建产物不存在: $dist_dir"
        exit 1
    fi

    if [[ -z "$SERVER_IP" || -z "$USERNAME" ]]; then
        log_error "远程部署需要配置 SERVER_IP 和 USERNAME"
        exit 1
    fi

    local ssh_opts="-p $SERVER_PORT -o StrictHostKeyChecking=no -o ConnectTimeout=10"

    if [[ "$AUTH_TYPE" == "key" && -n "$PRIVATE_KEY_PATH" && -f "$PRIVATE_KEY_PATH" ]]; then
        ssh_opts="$ssh_opts -i $PRIVATE_KEY_PATH"
    fi

    # 优先尝试 rsync
    if command_exists rsync; then
        log_info "使用 rsync 部署..."
        local rsync_ssh_opts="ssh $ssh_opts"

        if [[ "$AUTH_TYPE" == "password" && -n "$PASSWORD" ]]; then
            if command_exists sshpass; then
                if sshpass -p "$PASSWORD" rsync -avz --delete \
                    -e "$rsync_ssh_opts" \
                    "$dist_dir/" "$USERNAME@$SERVER_IP:$REMOTE_WEB_ROOT/"; then
                    log_ok "rsync 部署成功"
                    return
                fi
            else
                log_warn "未找到 sshpass，无法使用密码认证的 rsync"
            fi
        else
            if rsync -avz --delete \
                -e "$rsync_ssh_opts" \
                "$dist_dir/" "$USERNAME@$SERVER_IP:$REMOTE_WEB_ROOT/"; then
                log_ok "rsync 部署成功"
                return
            fi
        fi

        log_warn "rsync 失败，尝试 scp..."
    fi

    # 回退到 scp
    log_info "使用 scp 部署..."

    # 清空远程目录
    if [[ "$AUTH_TYPE" == "password" && -n "$PASSWORD" ]]; then
        if command_exists sshpass; then
            sshpass -p "$PASSWORD" ssh $ssh_opts "$USERNAME@$SERVER_IP" "rm -rf $REMOTE_WEB_ROOT/*"
            if sshpass -p "$PASSWORD" scp -r $ssh_opts "$dist_dir/"* "$USERNAME@$SERVER_IP:$REMOTE_WEB_ROOT/"; then
                log_ok "scp 部署成功"
                return
            fi
        else
            log_error "密码认证需要 sshpass，请先安装"
            exit 1
        fi
    else
        ssh $ssh_opts "$USERNAME@$SERVER_IP" "rm -rf $REMOTE_WEB_ROOT/*"
        if scp -r $ssh_opts "$dist_dir/"* "$USERNAME@$SERVER_IP:$REMOTE_WEB_ROOT/"; then
            log_ok "scp 部署成功"
            return
        fi
    fi

    log_error "远程部署失败"
    exit 1
}

deploy_remote() {
    log_info "远程构建并部署: $SERVER_IP:$REMOTE_BLOG_ROOT -> $REMOTE_WEB_ROOT"

    if [[ -z "$SERVER_IP" || -z "$USERNAME" ]]; then
        log_error "远程构建需要配置 SERVER_IP 和 USERNAME"
        exit 1
    fi

    local ssh_opts="-p $SERVER_PORT -o StrictHostKeyChecking=no -o ConnectTimeout=10"

    if [[ "$AUTH_TYPE" == "key" && -n "$PRIVATE_KEY_PATH" && -f "$PRIVATE_KEY_PATH" ]]; then
        ssh_opts="$ssh_opts -i $PRIVATE_KEY_PATH"
    fi

    # 远程执行构建
    log_info "在远程服务器执行构建..."

    local remote_cmds="
        set -e
        cd $REMOTE_BLOG_ROOT
        if [[ ! -d node_modules ]]; then
            echo 'Installing dependencies...'
            pnpm install
        fi
        echo 'Building...'
        pnpm build
        echo 'Deploying...'
        rm -rf $REMOTE_WEB_ROOT/*
        cp -r $REMOTE_BLOG_ROOT/dist/* $REMOTE_WEB_ROOT/
        echo 'Done'
    "

    if [[ "$AUTH_TYPE" == "password" && -n "$PASSWORD" ]]; then
        if ! command_exists sshpass; then
            log_error "密码认证需要 sshpass，请先安装"
            exit 1
        fi
        if ! sshpass -p "$PASSWORD" ssh $ssh_opts "$USERNAME@$SERVER_IP" "$remote_cmds"; then
            log_error "远程构建部署失败"
            exit 1
        fi
    else
        if ! ssh $ssh_opts "$USERNAME@$SERVER_IP" "$remote_cmds"; then
            log_error "远程构建部署失败"
            exit 1
        fi
    fi

    log_ok "远程构建部署完成"
}

# -----------------------------------------------------------------------------
# 主流程
# -----------------------------------------------------------------------------

main() {
    echo "========================================"
    echo "  AstrBot Firefly Blog Manager 部署脚本"
    echo "========================================"
    echo ""
    echo "部署模式: $DEPLOY_MODE"
    echo ""

    # 自动检测本地博客目录（仅 local_only 和 local_build 模式）
    if [[ "$DEPLOY_MODE" == "local_only" || "$DEPLOY_MODE" == "local_build" ]]; then
        LOCAL_BLOG_ROOT=$(find_local_blog_root)
        echo "本地博客目录: $LOCAL_BLOG_ROOT"
        echo ""
    fi

    # 1. Python 环境
    check_python

    # 2. pip 依赖
    check_pip_deps

    # 3. Node.js
    check_nodejs

    # 4. pnpm
    check_pnpm

    # 5. 根据模式执行后续步骤
    case "$DEPLOY_MODE" in
        local_only)
            check_firefly_project "$LOCAL_BLOG_ROOT"
            install_blog_deps "$LOCAL_BLOG_ROOT"
            build_blog "$LOCAL_BLOG_ROOT"
            deploy_local
            ;;

        local_build)
            check_firefly_project "$LOCAL_BLOG_ROOT"
            install_blog_deps "$LOCAL_BLOG_ROOT"
            build_blog "$LOCAL_BLOG_ROOT"
            deploy_local_to_remote
            ;;

        remote_build)
            # 远程构建模式只需要检测远程连接
            if [[ -z "$SERVER_IP" || -z "$USERNAME" ]]; then
                log_error "remote_build 模式需要 SERVER_IP 和 USERNAME"
                exit 1
            fi
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
}

# 加载配置文件（如果存在）
CONFIG_FILE="${SCRIPT_DIR}/deploy.conf"
if [[ -f "$CONFIG_FILE" ]]; then
    log_info "加载配置文件: $CONFIG_FILE"
    set -a
    source "$CONFIG_FILE"
    set +a
fi

main "$@"
