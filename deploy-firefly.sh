#!/bin/bash

# ============================================================
# Firefly 博客一键部署脚本 v2.0
# 支持 Linux / macOS / Windows (Git Bash)
# 作者: qiyueling2716
# 仓库: https://github.com/qiyueling2716/Firefly-Blog
# ============================================================

set -e

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# 检测操作系统
detect_os() {
    case "$OSTYPE" in
        msys*|cygwin*|mingw*)
            echo "windows"
            ;;
        darwin*)
            echo "macos"
            ;;
        linux-gnu*|linux-musl*)
            echo "linux"
            ;;
        *)
            # 检查 WSL 环境变量
            if [[ -n "$WSLENV" ]] || [[ -n "$WSL_INTEROP" ]]; then
                echo "wsl"
            else
                echo "unknown"
            fi
            ;;
    esac
}

OS_TYPE=$(detect_os)

# 简化的彩色输出（兼容 Windows）
if [[ "$OS_TYPE" == "windows" ]]; then
    # Windows Git Bash 下颜色可能不支持，降级为无颜色
    RED=''; GREEN=''; YELLOW=''; BLUE=''; NC=''
fi

print_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
print_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
print_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
print_error() { echo -e "${RED}[ERROR]${NC} $1"; }

show_banner() {
    echo "============================================================"
    echo "  🔥 Firefly 博客一键部署脚本 v2.0"
    echo "  作者: qiyueling2716"
    echo "  源码: https://github.com/qiyueling2716/Firefly-Blog"
    echo "  系统: $OS_TYPE"
    echo "============================================================"
    echo ""
}

# ============================================================
# 默认配置
# ============================================================
FIREFLY_REPO="https://github.com/qiyueling2716/Firefly-Blog.git"
FIREFLY_BRANCH="master"
BLOG_ROOT="/var/www/firefly"
NGINX_SITE_NAME="firefly"
DOMAIN=""
EMAIL=""
AUTO_SSL=false
INSTALL_NODE=true
INSTALL_NGINX=true
PORT=80

# Windows 专用配置
if [[ "$OS_TYPE" == "windows" ]]; then
    BLOG_ROOT="C:/var/www/firefly"
    PORT=8080  # Windows 下默认用 8080 避免权限问题
fi

# ============================================================
# 参数解析
# ============================================================
parse_args() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            --domain|-d)
                DOMAIN="$2"
                shift 2
                ;;
            --email|-e)
                EMAIL="$2"
                shift 2
                ;;
            --ssl)
                AUTO_SSL=true
                shift
                ;;
            --no-node)
                INSTALL_NODE=false
                shift
                ;;
            --no-nginx)
                INSTALL_NGINX=false
                shift
                ;;
            --blog-root)
                BLOG_ROOT="$2"
                shift 2
                ;;
            --port|-p)
                PORT="$2"
                shift 2
                ;;
            --branch|-b)
                FIREFLY_BRANCH="$2"
                shift 2
                ;;
            --help|-h)
                echo "用法: $0 [选项]"
                echo ""
                echo "选项:"
                echo "  -d, --domain DOMAIN     域名"
                echo "  -e, --email EMAIL       Let's Encrypt 邮箱"
                echo "  --ssl                   自动申请 SSL 证书"
                echo "  --no-node               跳过 Node.js 安装"
                echo "  --no-nginx              跳过 Web 服务器安装"
                echo "  --blog-root PATH        博客安装目录"
                echo "  -p, --port PORT         监听端口 (默认 Linux:80, Windows:8080)"
                echo "  -b, --branch BRANCH     克隆分支 (默认 master)"
                echo "  -h, --help              显示帮助"
                echo ""
                echo "示例:"
                echo "  $0 -d blog.example.com -e admin@example.com --ssl"
                echo "  $0 -p 8080 --no-nginx"
                exit 0
                ;;
            *)
                print_error "未知参数: $1"
                exit 1
                ;;
        esac
    done
}

# ============================================================
# 服务管理（兼容 Linux / Windows / macOS）
# ============================================================
service_action() {
    local action=$1
    local service=$2
    
    case "$OS_TYPE" in
        windows)
            # Windows 下用 net start / net stop
            case $action in
                start)   net start "$service" 2>/dev/null || true ;;
                stop)    net stop "$service" 2>/dev/null || true ;;
                reload)  net stop "$service" && net start "$service" 2>/dev/null || true ;;
                status)  sc query "$service" 2>/dev/null | grep -q "RUNNING" ;;
                *) return 1 ;;
            esac
            ;;
        macos)
            # macOS 用 brew services 或 launchctl
            if command -v brew &> /dev/null && brew services list | grep -q "$service"; then
                brew services "$action" "$service" 2>/dev/null || return 1
            else
                launchctl "$action" "homebrew.mxcl.$service" 2>/dev/null || return 1
            fi
            ;;
        linux|wsl)
            # Linux: 优先 systemd，其次 sysv
            if command -v systemctl &> /dev/null && systemctl list-units &> /dev/null 2>&1; then
                systemctl "$action" "$service" 2>/dev/null || return 1
            elif [ -f "/etc/init.d/$service" ]; then
                service "$service" "$action" 2>/dev/null || return 1
            else
                return 1
            fi
            ;;
        *)
            return 1
            ;;
    esac
    return 0
}

# ============================================================
# 检测并安装 Node.js
# ============================================================
install_node() {
    if [[ "$INSTALL_NODE" != true ]]; then
        print_info "跳过 Node.js 安装"
        return 0
    fi
    
    if command -v node &> /dev/null; then
        print_info "Node.js $(node -v) 已安装"
        return 0
    fi
    
    print_info "安装 Node.js..."
    
    case "$OS_TYPE" in
        windows)
            # Windows: 用 winget 安装
            if command -v winget &> /dev/null; then
                winget install OpenJS.NodeJS --silent
            else
                print_error "请安装 Node.js: https://nodejs.org/"
                exit 1
            fi
            ;;
        macos)
            # macOS: 用 brew 安装
            if command -v brew &> /dev/null; then
                brew install node
            else
                print_error "请安装 Homebrew 或 Node.js: https://nodejs.org/"
                exit 1
            fi
            ;;
        linux|wsl)
            # Linux: 使用 NodeSource 官方源
            curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
            apt install -y nodejs
            ;;
    esac
    
    print_success "Node.js $(node -v) 安装完成"
}

# ============================================================
# 检测并安装 pnpm
# ============================================================
install_pnpm() {
    if command -v pnpm &> /dev/null; then
        print_info "pnpm $(pnpm -v) 已安装"
        return 0
    fi
    
    print_info "安装 pnpm..."
    npm install -g pnpm
    print_success "pnpm $(pnpm -v) 安装完成"
}

# ============================================================
# 检测并安装 Git
# ============================================================
install_git() {
    if command -v git &> /dev/null; then
        return 0
    fi
    
    print_info "安装 Git..."
    case "$OS_TYPE" in
        windows)
            print_error "请安装 Git for Windows: https://git-scm.com/download/win"
            exit 1
            ;;
        macos)
            brew install git
            ;;
        linux|wsl)
            apt install -y git
            ;;
    esac
}

# ============================================================
# 检测并安装 Nginx / Caddy (Windows 下推荐 Caddy)
# ============================================================
install_web_server() {
    if [[ "$INSTALL_NGINX" != true ]]; then
        print_info "跳过 Web 服务器安装"
        return 0
    fi
    
    case "$OS_TYPE" in
        windows)
            # Windows 下使用 Caddy（轻量、自动 HTTPS）
            if ! command -v caddy &> /dev/null; then
                print_info "安装 Caddy..."
                # 下载 Caddy
                CADDY_URL="https://caddyserver.com/api/download?os=windows&arch=amd64"
                curl -L -o caddy.exe "$CADDY_URL"
                mkdir -p "C:/caddy"
                mv caddy.exe "C:/caddy/"
                # 添加到 PATH
                export PATH="C:/caddy:$PATH"
            fi
            ;;
        linux|wsl)
            # Linux: 安装 Nginx
            apt install -y nginx
            if service_action start nginx; then
                print_success "Nginx 已启动"
            else
                print_warn "Nginx 安装完成但未能自动启动"
            fi
            ;;
        macos)
            # macOS: 用 brew 安装 Nginx
            brew install nginx
            brew services start nginx
            ;;
    esac
}

# ============================================================
# 克隆并构建博客
# ============================================================
clone_and_build() {
    print_info "克隆 Firefly 源码 (分支: $FIREFLY_BRANCH)..."
    local tmp_dir
    case "$OS_TYPE" in
        windows) tmp_dir="/tmp" ;;
        *) tmp_dir="/tmp" ;;
    esac
    cd "$tmp_dir"
    rm -rf Firefly-Blog
    git clone -b "$FIREFLY_BRANCH" $FIREFLY_REPO Firefly-Blog
    cd Firefly-Blog

    print_info "安装依赖并构建..."
    pnpm install
    pnpm build

    print_info "创建博客目录并复制文件..."
    mkdir -p "$BLOG_ROOT"
    cp -r dist/* "$BLOG_ROOT/"
    print_success "博客构建完成，已部署到 $BLOG_ROOT"
}

# ============================================================
# 配置 Web 服务器
# ============================================================
configure_web_server() {
    print_info "配置 Web 服务器..."
    
    case "$OS_TYPE" in
        windows)
            # Windows: 使用 Python HTTP 服务器或 Caddy
            if command -v caddy &> /dev/null; then
                # 生成 Caddyfile
                cat > Caddyfile << EOF
:${PORT} {
    root * $BLOG_ROOT
    file_server
    try_files {path} {path}/ /index.html
}
EOF
                nohup caddy run --config Caddyfile > /tmp/caddy.log 2>&1 &
                print_success "Caddy 已启动在端口 $PORT"
            else
                # 用 Python 作为后备
                cd "$BLOG_ROOT"
                nohup python -m http.server $PORT > /tmp/blog.log 2>&1 &
                print_success "Python HTTP 服务器已启动在端口 $PORT"
            fi
            ;;
        linux|wsl)
            # Linux: 配置 Nginx
            local config_file="/etc/nginx/sites-available/$NGINX_SITE_NAME"
            
            # 检查是否有域名和 SSL
            if [[ -n "$DOMAIN" ]] && [[ "$AUTO_SSL" == true ]]; then
                # SSL 自动申请（需要 Certbot）
                if ! command -v certbot &> /dev/null; then
                    apt install -y certbot python3-certbot-nginx
                fi
                certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "$EMAIL"
            else
                # 生成普通配置
                cat > "$config_file" << EOF
server {
    listen ${PORT};
    server_name ${DOMAIN:-_};
    root $BLOG_ROOT;
    index index.html;

    location / {
        try_files \$uri \$uri/ /index.html;
    }

    location /_astro/ {
        expires 1y;
        add_header Cache-Control "public, immutable";
    }
}
EOF
                ln -sf "$config_file" "/etc/nginx/sites-enabled/"
                nginx -t && service_action reload nginx
            fi
            print_success "Nginx 配置完成"
            ;;
        macos)
            # macOS: 配置 Nginx
            local config_file="/usr/local/etc/nginx/servers/$NGINX_SITE_NAME.conf"
            cat > "$config_file" << EOF
server {
    listen ${PORT};
    server_name ${DOMAIN:-_};
    root $BLOG_ROOT;
    index index.html;

    location / {
        try_files \$uri \$uri/ /index.html;
    }

    location /_astro/ {
        expires 1y;
        add_header Cache-Control "public, immutable";
    }
}
EOF
            brew services restart nginx
            print_success "Nginx 配置完成"
            ;;
    esac
}

# ============================================================
# 输出部署信息
# ============================================================
print_summary() {
    echo ""
    echo "============================================================"
    print_success "🎉 Firefly 博客部署完成！"
    echo "============================================================"
    echo ""
    echo "📍 博客目录: $BLOG_ROOT"
    echo "📦 源码分支: $FIREFLY_BRANCH"
    echo "🖥️  系统: $OS_TYPE"
    echo ""
    
    local access_url
    if [[ -n "$DOMAIN" ]]; then
        if [[ "$AUTO_SSL" == true ]]; then
            access_url="https://$DOMAIN"
        else
            access_url="http://$DOMAIN:$PORT"
        fi
    else
        local ip
        ip=$(curl -s ifconfig.me || echo "localhost")
        access_url="http://$ip:$PORT"
    fi
    echo "🌐 访问地址: $access_url"
    echo ""
    echo "📝 后续操作:"
    echo "  1. 更新博客: cd /tmp/Firefly-Blog && git pull && pnpm build && cp -r dist/* $BLOG_ROOT/"
    echo "  2. 如需修改端口: 重新运行脚本 --port 新端口"
    echo "  3. 安装 AstrBot 插件: 在 WebUI 中安装 astrbot_plugin_Firefly_Blog_Manager"
    echo ""
    echo "============================================================"
}

# ============================================================
# 主流程
# ============================================================
main() {
    show_banner
    parse_args "$@"
    
    print_info "开始部署 Firefly 博客..."
    echo ""
    
    install_git
    install_node
    install_pnpm
    clone_and_build
    install_web_server
    configure_web_server
    print_summary
}

main "$@"
