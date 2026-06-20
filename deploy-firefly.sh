#!/bin/bash

# ============================================================
# Firefly 博客一键部署脚本
# 适用于 Ubuntu/Debian 服务器 (兼容 systemd 和 SysV init)
# 作者: qiyueling2716
# 仓库: https://github.com/qiyueling2716/Firefly-Blog
# ============================================================

set -e

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 默认配置
FIREFLY_REPO="https://github.com/qiyueling2716/Firefly-Blog.git"
FIREFLY_BRANCH="master"  # 改为 master
BLOG_ROOT="/var/www/firefly"
NGINX_SITE_NAME="firefly"
DOMAIN=""
EMAIL=""
AUTO_SSL=false
INSTALL_NODE=true
INSTALL_NGINX=true

print_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
print_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
print_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
print_error() { echo -e "${RED}[ERROR]${NC} $1"; }

show_banner() {
    echo "============================================================"
    echo "  🔥 Firefly 博客一键部署脚本 v1.0.0"
    echo "  作者: qiyueling2716"
    echo "  源码: https://github.com/qiyueling2716/Firefly-Blog"
    echo "============================================================"
    echo ""
}

check_root() {
    if [[ $EUID -ne 0 ]]; then
        print_error "此脚本需要 root 权限，请使用 sudo 运行"
        exit 1
    fi
}

check_os() {
    if [[ -f /etc/os-release ]]; then
        . /etc/os-release
        if [[ "$ID" != "ubuntu" && "$ID" != "debian" ]]; then
            print_warn "当前系统是 $NAME，脚本仅测试过 Ubuntu/Debian，可能不兼容"
            read -p "是否继续？(y/N) " -n 1 -r
            echo
            if [[ ! $REPLY =~ ^[Yy]$ ]]; then
                exit 1
            fi
        fi
    else
        print_warn "无法识别操作系统，可能不兼容"
    fi
}

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
            --branch|-b)
                FIREFLY_BRANCH="$2"
                shift 2
                ;;
            --help|-h)
                echo "用法: $0 [选项]"
                echo ""
                echo "选项:"
                echo "  -d, --domain DOMAIN     域名（如 blog.example.com）"
                echo "  -e, --email EMAIL       Let's Encrypt 邮箱（配合 --ssl 使用）"
                echo "  --ssl                   自动申请 SSL 证书"
                echo "  --no-node               不安装 Node.js（已安装可跳过）"
                echo "  --no-nginx              不安装 Nginx（已安装可跳过）"
                echo "  --blog-root PATH        博客安装目录（默认 /var/www/firefly）"
                echo "  -b, --branch BRANCH     指定克隆分支（默认 master）"
                echo "  -h, --help              显示此帮助信息"
                echo ""
                echo "示例:"
                echo "  $0 -d blog.example.com -e admin@example.com --ssl"
                echo "  $0 -d blog.example.com --no-node -b dev"
                exit 0
                ;;
            *)
                print_error "未知参数: $1"
                exit 1
                ;;
        esac
    done
}

# 服务管理函数（兼容 systemd 和 SysV init）
service_action() {
    local action=$1
    local service=$2
    if command -v systemctl &> /dev/null && systemctl list-units &> /dev/null 2>&1; then
        systemctl $action $service 2>/dev/null || return 1
    elif [ -f "/etc/init.d/$service" ]; then
        service $service $action 2>/dev/null || return 1
    else
        return 1
    fi
    return 0
}

install_packages() {
    print_info "更新软件包列表..."
    apt update -y

    print_info "安装必要工具..."
    apt install -y curl wget git unzip build-essential

    if [[ "$INSTALL_NGINX" == true ]]; then
        print_info "安装 Nginx..."
        apt install -y nginx
        
        print_info "尝试启动 Nginx..."
        if service_action start nginx; then
            print_success "Nginx 已启动"
        else
            print_warn "Nginx 安装完成但未能自动启动"
            print_warn "请手动执行: service nginx start  或   /etc/init.d/nginx start"
        fi
    fi

    if [[ "$INSTALL_NODE" == true ]]; then
        print_info "安装 Node.js 22.x..."
        curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
        apt install -y nodejs
        print_success "Node.js $(node -v) 安装完成"

        print_info "安装 pnpm..."
        npm install -g pnpm
        print_success "pnpm $(pnpm -v) 安装完成"
    else
        print_info "跳过 Node.js 安装"
    fi
}

clone_and_build() {
    print_info "克隆 Firefly 源码 (分支: $FIREFLY_BRANCH)..."
    cd /tmp
    rm -rf Firefly-Blog
    git clone -b "$FIREFLY_BRANCH" $FIREFLY_REPO Firefly-Blog
    cd Firefly-Blog

    print_info "安装依赖并构建..."
    pnpm install
    pnpm build

    print_info "创建博客目录并复制文件..."
    mkdir -p "$BLOG_ROOT"
    cp -r dist/* "$BLOG_ROOT/"
    
    # 尝试设置权限
    if id "www-data" &>/dev/null; then
        chown -R www-data:www-data "$BLOG_ROOT" 2>/dev/null || true
    fi

    print_success "博客构建完成，文件已部署到 $BLOG_ROOT"
}

configure_nginx() {
    print_info "配置 Nginx..."

    # 生成 Nginx 配置
    if [[ -n "$DOMAIN" ]]; then
        if [[ "$AUTO_SSL" == true ]]; then
            if [[ -z "$EMAIL" ]]; then
                print_error "启用 SSL 但未提供邮箱，请使用 -e 参数"
                exit 1
            fi
            print_info "安装 Certbot..."
            apt install -y certbot python3-certbot-nginx
            print_info "自动申请 SSL 证书..."
            certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "$EMAIL"
            print_success "SSL 证书申请完成"
        else
            cat > /etc/nginx/sites-available/$NGINX_SITE_NAME << EOF
server {
    listen 80;
    server_name $DOMAIN;
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
            print_success "HTTP 配置创建完成"
        fi
    else
        print_warn "未指定域名，将使用 IP/端口配置"
        cat > /etc/nginx/sites-available/$NGINX_SITE_NAME << EOF
server {
    listen 80;
    server_name _;
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
        print_success "默认配置创建完成"
    fi

    # 启用站点
    ln -sf /etc/nginx/sites-available/$NGINX_SITE_NAME /etc/nginx/sites-enabled/
    
    # 测试配置
    print_info "测试 Nginx 配置..."
    if nginx -t; then
        print_info "重载 Nginx..."
        if ! service_action reload nginx; then
            print_warn "无法自动重载 Nginx，请手动执行: service nginx reload"
        fi
    else
        print_error "Nginx 配置测试失败，请检查配置文件"
        exit 1
    fi

    print_success "Nginx 配置完成"
}

print_summary() {
    echo ""
    echo "============================================================"
    print_success "🎉 Firefly 博客部署完成！"
    echo "============================================================"
    echo ""
    echo "📍 博客目录: $BLOG_ROOT"
    echo "📦 源码分支: $FIREFLY_BRANCH"
    echo ""

    if [[ -n "$DOMAIN" ]]; then
        if [[ "$AUTO_SSL" == true ]]; then
            echo "🌐 访问地址: https://$DOMAIN"
        else
            echo "🌐 访问地址: http://$DOMAIN"
        fi
    else
        IP=$(curl -s ifconfig.me || echo "服务器IP")
        echo "🌐 访问地址: http://$IP"
        echo "⚠️  建议绑定域名并启用 SSL"
    fi

    echo ""
    echo "📝 后续操作:"
    echo "  1. 如需手动更新: cd /tmp/Firefly-Blog && git pull && pnpm build && cp -r dist/* $BLOG_ROOT/"
    echo "  2. Nginx 日志: /var/log/nginx/"
    echo "  3. 安装 AstrBot 插件: 在 WebUI 中安装 astrbot_plugin_Firefly_Blog_Manager"
    echo ""
    echo "============================================================"
}

# ==================== 主流程 ====================
main() {
    show_banner
    parse_args "$@"
    check_root
    check_os

    print_info "开始部署 Firefly 博客..."
    echo ""

    install_packages
    clone_and_build
    configure_nginx
    print_summary
}

main "$@"
