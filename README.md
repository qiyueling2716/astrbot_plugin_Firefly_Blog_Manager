# AstrBot Firefly Blog Manager

让 AstrBot 的 AI Agent 通过自然语言指令管理你的 [Firefly 博客](https://github.com/qiyueling2716/Firefly-Blog)。

## 功能特性

### 文章管理

| 操作 | 示例指令 |
|------|----------|
| 创建文章 | "帮我写一篇 Docker 入门，标题叫 Docker 快速入门" |
| 删除文章 | "删除文章 Docker 快速入门" |
| 列出文章 | "列出我的博客所有文章" |
| 查看内容 | "查看 Docker 快速入门 的内容" |
| 更新文章 | "把 Docker 快速入门 改成 Kubernetes 入门" |
| 搜索文章 | "搜索包含 Docker 的文章" |

### 投稿系统

| 操作 | 说明 |
|------|------|
| 提交投稿 | 任何人可提交文章草稿，管理员审核后发布 |
| 查看投稿 | 管理员查看所有投稿列表及详情 |
| 批准/拒绝 | 管理员审核通过后发布，或拒绝并附修改意见 |
| 撤回投稿 | 投稿者可撤回自己提交的投稿 |
| AI 初审 | 开启后可自动调用 LLM 评估投稿质量，给出评分和建议 |

### 进阶语法

| 语法 | 说明 |
|------|------|
| GitHub 仓库卡片 | 嵌入 GitHub 仓库动态信息 |
| Admonitions | 多种提醒框（NOTE/TIP/IMPORTANT/WARNING/CAUTION） |
| Spoiler | 可折叠的剧透文本 |
| 图片画廊 | 2-4 张图片并排展示 |
| Expressive Code | 代码块行号、高亮、折叠 |
| Mermaid | 流程图、时序图、甘特图等 |
| PlantUML | 活动图、用例图、C4 架构图等 |
| KaTeX | 数学公式渲染 |

### 构建与部署

| 操作 | 说明 |
|------|------|
| 检查环境 | 检查 Node.js 和 pnpm 是否已安装 |
| 安装依赖 | 执行 `pnpm install` |
| 构建博客 | 执行 `pnpm build` 生成静态站点 |
| 部署博客 | 把 `dist/` 部署到 Web 服务器 |
| 一键构建部署 | 自动执行环境检查 → 依赖安装 → 构建 → 部署 |

## 显式指令

用户可以直接使用以下命令（以 `/` 开头）：

| 指令 | 别名 | 权限 | 说明 |
|------|------|------|------|
| `/博客帮助` | `博客菜单`、`帮助` | 公开 | 查看所有可用指令和工具 |
| `/博客列表` | `博客文章`、`列出文章` | 公开 | 列出所有博客文章 |
| `/博客搜索 <关键词>` | `搜索文章` | 公开 | 搜索博客文章 |
| `/博客投稿` | `投稿`、`提交投稿` | 公开 | 提交文章投稿 |
| `/我的投稿` | `我的草稿` | 公开 | 查看自己提交的投稿状态 |
| `/博客环境` | `检查环境` | 管理员 | 检查 Node.js 和 pnpm 是否安装 |
| `/博客构建` | `构建博客` | 管理员 | 构建博客（占用约 1.5GB 内存） |
| `/博客部署` | `部署博客` | 管理员 | 部署博客到服务器 |
| `/博客投稿列表` | `投稿列表`、`待审核投稿` | 管理员 | 查看待审核的投稿 |
| `/内存状态` | `检查内存` | 公开 | 检查当前内存状态 |

## 系统要求

| 组件 | 最低版本 | 说明 |
|------|----------|------|
| AstrBot | >= 4.16 | 插件运行环境 |
| Python | >= 3.10 | 插件依赖 |
| Node.js | >= 22 | Firefly 博客构建需要 |
| pnpm | 任意 | Firefly 依赖管理 |

## 资源占用提醒

### 内存需求

Firefly 博客构建过程（`pnpm build`）会占用约 **1.5GB 内存**，这是 Astro 框架的正常行为。

### 缓解方案

1. **选择合适的部署模式**
   - 如果 AstrBot 服务器内存有限（如 < 2GB），建议使用 `remote_build` 模式，让远端服务器承担构建工作
   - 如果使用 `local_build` 或 `local_only` 模式，确保服务器至少有 2GB 可用内存

2. **限制构建并发**
   - 在插件配置中设置 `build_memory_threshold`（内存阈值，单位 MB）
   - 当可用内存低于阈值时，插件会自动跳过构建并提示用户

3. **定时构建**
   - 将构建任务安排在服务器空闲时段执行
   - 避免在高负载情况下触发构建

## 部署模式

插件支持三种模式，覆盖绝大多数部署场景：

| 模式 | 构建位置 | 部署位置 | 适用场景 |
|------|----------|----------|----------|
| `local_build` | AstrBot 所在服务器 | 通过 SSH 推送到远端 | AstrBot 服务器性能好，远端只跑静态服务 |
| `remote_build` | 通过 SSH 在远端服务器构建 | 远端服务器 | AstrBot 跑在轻量设备上（如树莓派），远端性能更好 |
| `local_only` | AstrBot 所在服务器 | 同一台机器的本地目录 | 单服务器部署 |

### 模式选择指南

- **local_build**：AstrBot 在云服务器上，远端是低配 VPS 或静态托管
- **remote_build**：AstrBot 在本地电脑/树莓派，远端是性能更好的服务器
- **local_only**：博客和 AstrBot 装在同一台机器上

## 安装

### 通过 AstrBot WebUI（推荐）

1. 打开 AstrBot WebUI → 插件管理
2. 点击「安装插件」
3. 输入仓库地址 `https://github.com/qiyueling2716/astrbot_plugin_Firefly_Blog_Manager`
4. 点击安装，然后重载插件

### 手动安装

```bash
cd AstrBot/data/plugins
git clone https://github.com/qiyueling2716/astrbot_plugin_Firefly_Blog_Manager.git
cd astrbot_plugin_Firefly_Blog_Manager
pip install -r requirements.txt
```

然后在 AstrBot WebUI 中重载插件。

## 配置

在 AstrBot WebUI → 插件管理 → Firefly 博客管理 → 配置。

### 配置项说明

| 配置项 | 类型 | 说明 | 哪些模式需要 |
|--------|------|------|-------------|
| `deploy_mode` | 下拉 | `local_build` / `remote_build` / `local_only` | 全部 |
| `local_blog_root` | 字符串 | 本地 Firefly 博客根目录（含 `package.json`） | `local_build`, `local_only` |
| `web_root` | 字符串 | 本地 Web 服务器根目录 | `local_only` |
| `server_ip` | 字符串 | 远程服务器 IP | `local_build`, `remote_build` |
| `server_port` | 整数 | SSH 端口，默认 22 | `local_build`, `remote_build` |
| `username` | 字符串 | SSH 登录用户名 | `local_build`, `remote_build` |
| `auth_type` | 下拉 | `key`（密钥）或 `password`（密码） | `local_build`, `remote_build` |
| `private_key_path` | 字符串 | 本地私钥文件绝对路径 | `key` 认证时 |
| `password` | 字符串 | SSH 登录密码 | `password` 认证时 |
| `ssh_known_hosts_path` | 字符串 | SSH known_hosts 文件路径，留空使用默认 `~/.ssh/known_hosts` | `local_build`, `remote_build` |
| `ssh_strict_host_key_checking` | 布尔 | 是否启用严格的 SSH 主机密钥验证，关闭时使用 `accept-new` | `local_build`, `remote_build` |
| `remote_blog_root` | 字符串 | 远端服务器上 Firefly 博客根目录 | `remote_build` |
| `remote_web_root` | 字符串 | 远端 Web 服务器根目录 | `local_build`, `remote_build` |
| `build_memory_threshold` | 整数 | 构建内存阈值（MB），默认 1536 | 全部 |
| `build_memory_limit` | 整数 | 构建内存限制（MB），0 表示不限制 | 全部 |
| `allow_build_concurrent` | 布尔 | 是否允许并发构建，默认 false | 全部 |
| `allow_only_owner` | 布尔 | 是否只允许管理员使用非构建类工具，默认 false | 全部 |
| `owner_user_id` | 字符串 | 主人用户 ID（回退配置），插件优先使用 AstrBot 框架管理员系统 | 全部 |
| `admin_users` | 列表 | 管理员用户 ID 列表（回退配置），插件优先使用 AstrBot 框架管理员系统 | 全部 |
| `enable_advanced_syntax` | 布尔 | 是否启用进阶 Markdown 语法功能，默认开启 | 全部 |
| `advanced_syntax_*` | 布尔 | 8 个进阶语法子开关（github_card / admonitions / spoiler / image_grid / code_blocks / mermaid / plantuml / katex），默认均开启 | 全部 |
| `enable_ai_review` | 布尔 | 是否启用 AI 初审功能，开启后投稿提交时自动评估内容质量，默认开启 | 全部 |

### 权限控制说明

插件**优先复用 AstrBot 框架的管理员系统**，不再需要单独配置管理员：

| 工具类型 | 权限要求 | 说明 |
|----------|----------|------|
| **构建相关工具** | 始终需要管理员权限 | `build_blog`、`deploy_blog`、`build_and_deploy_blog`、`install_blog_dependencies`、`auto_setup_blog` 等工具始终只能由管理员使用 |
| **投稿工具** | 任何人都可以使用 | `submit_post_draft`、`retract_submission` 工具允许任何人提交和撤回投稿，无需权限验证 |
| **投稿审核工具** | 始终需要管理员权限 | `list_post_submissions`、`review_submission`、`approve_submission`、`reject_submission`、`delete_submission`、`ai_review_submission` 等工具始终只能由管理员使用 |
| **文章管理工具** | 受 `allow_only_owner` 配置控制 | `create_blog_post`、`delete_blog_post`、`update_blog_post` 等工具在 `allow_only_owner=true` 时仅管理员可用 |

**权限验证顺序**：

1. 优先使用 AstrBot 框架提供的权限检查方法（`event.is_admin()`、`event.is_owner()`、`context.is_admin()`、`context.is_owner()`）
2. 当框架方法不可用时，回退到插件配置文件中的 `admin_users` 和 `owner_user_id`

**权限配置示例**：

```yaml
# 允许所有人投稿，但文章管理仅管理员可用
allow_only_owner: true

# 以下配置仅作为回退方案，通常不需要配置
# owner_user_id: ""  # 留空即可，插件会自动使用 AstrBot 的主人 ID
# admin_users: []     # 留空即可，插件会自动使用 AstrBot 的管理员列表
```

### AI 初审功能

开启 `enable_ai_review` 后，每次有新投稿提交时，插件会自动调用 LLM 对投稿内容进行初审评估，包括：

- **内容质量评分**（0-10 分）
- **优点分析**：文章亮点
- **问题发现**：格式、完整性、可读性等问题
- **改进建议**：具体优化方向
- **过审/打回建议**：AI 综合判断是否建议发布

初审结果会持久化存储，管理员在查看投稿列表时可以看到每篇投稿的 AI 初审状态，查看详情时可以看到完整的评估报告。管理员可选择：

1. **过审**：直接批准发布
2. **复审**：查看全文后再决定是否过审

关闭 AI 初审后，新投稿仅发送提醒通知，管理员需自行查看并审核。

### 进阶语法

开启 `enable_advanced_syntax` 后，LLM 在创建文章时可使用 Firefly 博客支持的进阶 Markdown 语法增强文章表现力。每个语法子项均可独立开关，便于精确控制。

如需了解各语法的使用方法，可在对话中询问 `list_advanced_syntax` 或 `get_syntax_guide`。详见 [Firefly 博客文章示例](https://firefly.ysysaily.xyz/archive/?category=文章示例)。

### 路径配置详解

**`local_blog_root`** — Firefly 博客项目的根目录，必须包含 `package.json` 和 `src/content/posts/`。

示例：
- Linux: `/var/www/firefly`
- Windows: `D:\www\firefly`

**`web_root` / `remote_web_root`** — Firefly 博客的部署目录（不是 Nginx 的网站根目录）。构建产物 `dist/` 会被复制到这个目录。

示例：
- Firefly 博客部署目录: `/var/www/firefly`
- 注意：这是博客的实际部署位置，不是 Nginx 配置的 `root` 路径

**如何确认你的部署目录？**

SSH 到服务器执行：
```bash
# 查看 Firefly 博客目录结构
ls -la /var/www/firefly/

# 查看 Nginx 配置的 root 路径
grep -r "root" /etc/nginx/sites-enabled/ /etc/nginx/conf.d/ 2>/dev/null | head -5

# 如果 Nginx root 是 /var/www/html，检查是否是指向 firefly 的软链接或别名
ls -la /var/www/html
```

部署后，部署目录下应该包含：
```
/var/www/firefly/
├── index.html          <- 博客首页
├── assets/             <- 静态资源
└── posts/              <- 文章页面
```

**重要**：确保 Nginx/Apache 配置的 `root` 路径指向这个部署目录，或者该目录本身就是 Web 服务器的根目录。

## SSH 认证配置

### 密钥认证（推荐）

1. 生成密钥对：
   ```bash
   ssh-keygen -t ed25519 -C "astrbot-plugin"
   ```
2. 把公钥复制到服务器：
   ```bash
   ssh-copy-id -i ~/.ssh/id_ed25519.pub user@服务器IP
   ```
3. 配置 `auth_type` 为 `key`，`private_key_path` 填私钥路径

**注意！请确保目标服务器允许了密钥登陆！**

### 密码认证

配置 `auth_type` 为 `password`，`password` 填 SSH 密码即可。

## 一键部署脚本

插件目录下提供了 `deploy.sh`（Linux/macOS）和 `deploy.ps1`（Windows）脚本。这些脚本**独立运行**，不依赖 AstrBot，适合在服务器上直接执行构建和部署。

### 脚本与插件的区别

| | 一键部署脚本 | AstrBot 插件 |
|--|-------------|-------------|
| 运行方式 | 命令行直接执行 | 通过 AstrBot WebUI 或 AI 对话触发 |
| 依赖环境 | 不需要 AstrBot | 需要 AstrBot >= 4.16 |
| 适用场景 | 服务器初始化、CI/CD | 日常文章管理、AI 对话控制 |
| 功能范围 | 仅构建和部署 | 文章管理 + 构建部署 |

### 脚本功能

- 检测 Python >= 3.10（缺失时自动安装）
- 检测并自动安装 pip 依赖（asyncssh, pyyaml）
- 检测 Node.js >= 22（缺失时自动安装）
- 检测 pnpm（缺失时自动安装）
- 自动克隆 Firefly 博客仓库（支持镜像加速）
- 自动执行 `pnpm install` 和 `pnpm build`
- 根据部署模式执行对应部署操作
- **自动配置 Nginx 或 Apache Web 服务器**
- **支持 SSL 证书配置（HTTPS）**
- **资源监控警告（CPU/内存）**
- **构建过程资源日志记录**
- 自动备份旧版本

### Linux / macOS

```bash
# 1. 复制配置文件模板
cp deploy.conf.example deploy.conf

# 2. 编辑 deploy.conf，填写实际值
# DEPLOY_MODE=local_only
# LOCAL_BLOG_ROOT=/var/www/firefly
# WEB_ROOT=/var/www/html

# 3. 执行部署
chmod +x deploy.sh
./deploy.sh
```

### Windows

```powershell
# 1. 复制配置文件模板
Copy-Item deploy.conf.example deploy.conf

# 2. 编辑 deploy.conf，填写实际值
# DEPLOY_MODE=local_only
# LOCAL_BLOG_ROOT=D:\www\firefly
# WEB_ROOT=D:\www\html

# 3. 执行部署
powershell -File .\deploy.ps1
```

### deploy.conf 配置示例

**单服务器部署（local_only）**
```bash
DEPLOY_MODE=local_only
LOCAL_BLOG_ROOT=/var/www/firefly
WEB_ROOT=/var/www/html
```

**本地构建 + 远程部署（local_build）**
```bash
DEPLOY_MODE=local_build
LOCAL_BLOG_ROOT=/var/www/firefly
SERVER_IP=192.168.1.100
USERNAME=admin
AUTH_TYPE=key
PRIVATE_KEY_PATH=/home/admin/.ssh/id_ed25519
REMOTE_WEB_ROOT=/var/www/html
```

**远程构建 + 远程部署（remote_build）**
```bash
DEPLOY_MODE=remote_build
SERVER_IP=192.168.1.100
USERNAME=admin
AUTH_TYPE=key
PRIVATE_KEY_PATH=/home/admin/.ssh/id_ed25519
REMOTE_BLOG_ROOT=/var/www/firefly
REMOTE_WEB_ROOT=/var/www/html
```

### 通过环境变量覆盖配置

不创建 `deploy.conf` 也可以直接传环境变量：

```bash
# Linux / macOS
DEPLOY_MODE=local_build \
  LOCAL_BLOG_ROOT=/var/www/firefly \
  SERVER_IP=192.168.1.100 \
  USERNAME=admin \
  AUTH_TYPE=key \
  PRIVATE_KEY_PATH=/home/admin/.ssh/id_ed25519 \
  REMOTE_WEB_ROOT=/var/www/firefly \
  ./deploy.sh
```

```powershell
# Windows
$env:DEPLOY_MODE="local_build"
$env:LOCAL_BLOG_ROOT="D:\www\firefly"
$env:SERVER_IP="192.168.1.100"
$env:USERNAME="admin"
$env:AUTH_TYPE="key"
$env:REMOTE_WEB_ROOT="D:\www\firefly"
powershell -File .\deploy.ps1
```

## 常见问题

### Q: `web_root` 和 `remote_web_root` 应该填什么路径？

填 Firefly 博客的部署目录，不是 Nginx 配置的网站根目录。

常见值：
- Firefly 博客部署目录: `/var/www/firefly`
- 注意：这是博客构建产物部署的位置，Nginx 的 `root` 应该指向这里

不确定的话，SSH 到服务器执行 `ls -la /var/www/firefly/` 查看。

### Q: 如何启用 HTTPS？

1. 准备 SSL 证书文件（`.crt`）和密钥文件（`.key`）
2. 在 `deploy.conf` 中配置：
   ```bash
   DOMAIN_NAME=blog.example.com
   ENABLE_HTTPS=true
   SSL_CERT_PATH=/etc/ssl/certs/your_cert.crt
   SSL_CERT_KEY_PATH=/etc/ssl/private/your_key.key
   WEB_SERVER=nginx
   ```
3. 脚本会自动配置 HTTPS 并将 HTTP 重定向到 HTTPS

### Q: 如何选择 Nginx 或 Apache？

设置 `WEB_SERVER` 配置项：
- `WEB_SERVER=nginx` — 使用 Nginx（默认）
- `WEB_SERVER=apache` — 使用 Apache
- `WEB_SERVER=none` — 不自动配置 Web 服务器

### Q: 资源监控功能是什么？

部署脚本提供了资源监控功能，在安装依赖和构建前会检查系统资源使用情况：

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `RESOURCE_WARNING_CPU` | CPU 使用率警告阈值 | 80% |
| `RESOURCE_WARNING_MEM` | 内存使用率警告阈值 | 85% |

当资源使用率超过阈值时，脚本会显示警告并询问是否继续。构建过程中还会后台记录资源使用日志到 `build_resource_monitor.log`。

### Q: `local_blog_root` 和 `web_root` 有什么区别？

- `local_blog_root` — Firefly 博客项目的根目录，包含 `package.json` 和 `src/content/posts/`，是构建的**源**
- `web_root` — Firefly 博客部署目录，构建产物 `dist/` 会复制到这里

示例：
```
/var/www/firefly/          <- local_blog_root（博客源码目录）
├── package.json
├── src/content/posts/
└── dist/                  <- 构建产物

/var/www/firefly/          <- web_root（部署目录，与源码目录相同）
├── index.html             <- 从 dist/ 复制过来
├── assets/
└── posts/
```

### Q: 构建时内存不足怎么办？

选 `remote_build` 模式，让远端服务器承担构建工作。

### Q: pnpm 依赖没装怎么办？

插件会自动检测。如果 `node_modules` 不存在，执行构建时会提示你先运行 `install_blog_dependencies`。

### Q: SSH 断联怎么办？

插件使用 asyncssh，自带 keepalive（30 秒间隔，最多 3 次重试）。如果连接断开，下次操作时会自动重连。

### Q: 文章修改后网站没变化？

Firefly 是静态博客，修改文章后必须重新构建并部署才会生效。

## 技术细节

- 文章格式：完整支持 Firefly 的 YAML Front-matter
- 远程操作：基于 asyncssh，异步非阻塞，带连接保活和自动重连
- 主机密钥验证：默认启用 SSH 主机密钥验证，防止中间人攻击
- 密码安全：密码通过临时文件传递，不出现在进程列表和 shell 命令中
- 命令注入防护：部署命令参数使用 `shlex.quote()` 转义，执行前检测危险 shell 模式
- 元数据解析：PyYAML
- 构建超时：10 分钟
- 部署超时：5 分钟
- 部署传输：优先 rsync，失败自动回退到 scp

## 依赖

- `asyncssh>=2.14.0` — 异步 SSH 连接
- `pyyaml>=6.0` — YAML 解析与生成

## 项目结构

```
astrbot_plugin_Firefly_Blog_Manager/
├── main.py                 # 插件主文件（AstrBot 加载入口）
├── metadata.yaml           # 插件元数据
├── _conf_schema.json       # 插件配置定义（AstrBot WebUI 使用）
├── requirements.txt        # Python 依赖
├── deploy.sh               # Linux/macOS 一键部署脚本
├── deploy.ps1              # Windows 一键部署脚本（PowerShell）
├── deploy.conf.example     # 部署脚本配置模板
├── CHANGELOG.md            # 版本变更记录
├── README.md               # 本文档
└── LICENSE                 # MIT 许可证
```

## 开发者

- 作者：月凌
- 仓库：https://github.com/qiyueling2716/astrbot_plugin_Firefly_Blog_Manager
- 许可证：MIT