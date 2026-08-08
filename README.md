# AstrBot Firefly Blog Manager

让 AstrBot 的 AI Agent 通过自然语言指令管理你的 [Firefly 博客](https://github.com/qiyueling2716/Firefly-Blog)，并提供内置 Dashboard 管理页面。

## 功能总览

- **文章管理**：创建、删除、列出、查看、更新、搜索文章（YAML Front-matter 完整支持）
- **投稿系统**：任何人可投稿，管理员审核后发布；支持 AI 初审（自动评分与建议）
- **构建部署**：环境检查 → 依赖安装 → 构建 → 部署（rsync/scp，自动回退）
- **进阶语法**：GitHub 卡片、Admonitions、Spoiler、图片画廊、Expressive Code、Mermaid、PlantUML、KaTeX（可独立开关）
- **WebUI 管理页面**：博客状态、src 文件管理、对外展示配置编辑、随机壁纸背景

## 系统要求

| 组件 | 最低版本 | 说明 |
|------|----------|------|
| AstrBot | >= 4.16 | 插件运行环境 |
| Python | >= 3.10 | 插件依赖 |
| Node.js | >= 22 | Firefly 博客构建需要（由构建/部署脚本自动检查） |

> 构建 `pnpm build` 约占用 **1.5GB 内存**。AstrBot 服务器内存不足（< 2GB）时请使用 `remote_build` 模式，让远端承担构建。

## 安装

```bash
cd AstrBot/data/plugins
git clone https://github.com/qiyueling2716/astrbot_plugin_Firefly_Blog_Manager.git
cd astrbot_plugin_Firefly_Blog_Manager
pip install -r requirements.txt
```

然后在 AstrBot WebUI → 插件管理 中重载插件。也可直接在 WebUI 中按仓库地址在线安装。

## 使用方式

### 自然语言（推荐）

直接对 AI 说话即可：*"帮我写一篇 Docker 入门"*、*"删除文章 Docker 快速入门"*、*"列出所有文章"*。

### 显式指令

| 指令 | 权限 | 说明 |
|------|------|------|
| `/博客帮助` | 公开 | 查看所有可用指令和工具 |
| `/博客列表` | 公开 | 列出所有博客文章 |
| `/博客搜索 <关键词>` | 公开 | 搜索博客文章 |
| `/博客投稿` | 公开 | 提交文章投稿 |
| `/我的投稿` | 公开 | 查看自己的投稿状态 |
| `/内存状态` | 公开 | 检查当前内存状态 |
| `/博客环境` | 管理员 | 检查 Node.js 和 pnpm |
| `/博客构建` | 管理员 | 构建博客 |
| `/博客部署` | 管理员 | 部署博客到服务器 |
| `/博客投稿列表` | 管理员 | 查看待审核投稿 |

## 部署模式

| 模式 | 构建位置 | 部署位置 | 适用场景 |
|------|----------|----------|----------|
| `local_build` | AstrBot 服务器 | SSH 推送到远端 `remote_web_root` | AstrBot 在云服务器，远端是低配 VPS |
| `remote_build` | 远端服务器（SSH） | 远端仓库构建后复制到 `remote_web_root` | AstrBot 在本地/树莓派，远端性能更好 |
| `local_only` | AstrBot 服务器 | 同一台机器的 `web_root` | 单服务器部署 |

**路径约定**：
- `local_blog_root`：博客**源码**根目录（含 `package.json` 与 `src/content/posts/`），构建的源
- `web_root` / `remote_web_root`：博客**部署目录**（构建产物 `dist/` 复制到此，Nginx root 应指向这里）

## 一键部署脚本

插件目录的 `deploy.sh`（Linux/macOS）与 `deploy.ps1`（Windows）**独立于 AstrBot 运行**，适合服务器初始化 / CI/CD。脚本自动检测并安装 Python、Node.js（>=22）、pnpm 与依赖，克隆博客仓库、构建部署，可选自动配置 Nginx/Apache、HTTPS、资源监控与备份。

```bash
cp deploy.conf.example deploy.conf   # 编辑填写实际值
chmod +x deploy.sh && ./deploy.sh
```

不创建配置文件时可直接传环境变量（如 `DEPLOY_MODE=local_build LOCAL_BLOG_ROOT=/var/www/firefly ./deploy.sh`）。

### 网络加速（下载慢 / 卡住时）

国内网络环境建议设置 `USE_CN_MIRROR=1`：克隆自动依次尝试内置 GitHub 镜像（每个源有超时），npm/pnpm 自动切换到 npmmirror 源；官方源失败时自动重试镜像源。其他可调项：

| 配置项 | 默认 | 说明 |
|--------|------|------|
| `USE_CN_MIRROR` | `0` | 设为 `1` 启用国内镜像加速 |
| `GITHUB_MIRROR` | 空 | 手动指定克隆镜像（如 `https://ghfast.top/https://github.com`） |
| `NPM_REGISTRY` | 空 | 自定义 npm/pnpm 源 |
| `CLONE_DEPTH` | `1` | 浅克隆（完整克隆设 `0`） |
| `GIT_CLONE_TIMEOUT` | `120` | 单个克隆源超时秒数 |
| `REMOTE_CMD_TIMEOUT` | `3600` | 远程构建总超时（remote_build） |
| `AUTO_YES` | `0` | 设为 `1` 跳过所有交互确认（CI 自动生效） |

所有下载、克隆、SSH/rsync 操作均带超时，非交互环境（CI / 后台）不会卡在确认提示上。

HTTPS（`ENABLE_HTTPS` + 证书路径）、Web 服务器（`WEB_SERVER=nginx|apache|none`）、资源告警阈值（`RESOURCE_WARNING_CPU/MEM`）等均可在 `deploy.conf` 中配置，示例见 `deploy.conf.example`。

## SSH 认证

1. **密钥认证（推荐）**：`ssh-keygen -t ed25519` 生成密钥，`ssh-copy-id` 复制公钥到服务器，配置 `auth_type=key` 与 `private_key_path`。请确保服务器允许密钥登录
2. **密码认证**：配置 `auth_type=password` 与 `password` 即可

## WebUI 管理页面

在 AstrBot Dashboard「插件管理 → 插件详情 → 页面」中打开。包含三个标签页：

- **状态**：部署模式、博客检测结果、构建状态、文章数量、内存状态；未检测到博客时可手动指定根目录或重新自动检测
- **文件管理**：树形浏览博客 `src/` 目录，在线编辑/新建/删除/上传。图片等二进制文件以预览方式查看。`remote_build` 模式下操作远端仓库
- **对外展示**：表格化编辑 friendsConfig.ts / socialConfig.ts / footerConfig.ts（可在插件配置中追加）等对外展示配置，只重建对应数组块，文件其余内容原样保留

**壁纸背景**：打开页面时自动从博客 `src/assets/images/DesktopWallpaper`（桌面端）或 `MobileWallpaper`（移动端）随机选取一张作为背景。**未检测到已部署的博客时**，页面顶部会提示使用插件目录下的 `deploy.sh` / `deploy.ps1` 部署。

> 安全：文件操作严格限定在博客 `src/` 目录内（路径穿越、绝对路径均被拒绝）；页面与 Web API 由 AstrBot 内置鉴权保护（Dashboard 登录 + plugin 权限域）。

## 配置项

| 配置项 | 类型 | 说明 |
|--------|------|------|
| `deploy_mode` | 下拉 | `local_build` / `remote_build` / `local_only` |
| `local_blog_root` | 字符串 | 本地博客源码根目录（含 `package.json`） |
| `web_root` | 字符串 | 本地部署目录（`local_only`） |
| `server_ip` / `server_port` / `username` | 字符串/整数/字符串 | 远程服务器 SSH 信息 |
| `auth_type` | 下拉 | `key` 或 `password` |
| `private_key_path` / `password` | 字符串 | SSH 私钥路径 / 密码 |
| `ssh_known_hosts_path` | 字符串 | known_hosts 路径，留空用默认 |
| `ssh_strict_host_key_checking` | 布尔 | 严格主机密钥校验（默认开，关闭时用 `accept-new`） |
| `remote_blog_root` | 字符串 | 远端博客源码根目录（`remote_build`） |
| `remote_web_root` | 字符串 | 远端部署目录 |
| `build_memory_threshold` | 整数 | 内存低于该值（MB）时跳过构建，默认 1536 |
| `build_memory_limit` | 整数 | 构建内存限制（MB），0 不限制 |
| `allow_build_concurrent` | 布尔 | 是否允许并发构建 |
| `allow_only_owner` | 布尔 | 是否只允许管理员使用非构建类工具 |
| `owner_user_id` / `admin_users` | 字符串/列表 | 管理员回退配置（优先用 AstrBot 框架权限系统） |
| `enable_advanced_syntax` | 布尔 | 进阶 Markdown 语法总开关 |
| `advanced_syntax_*` | 布尔 | 8 个子开关（github_card / admonitions / spoiler / image_grid / code_blocks / mermaid / plantuml / katex） |
| `enable_ai_review` | 布尔 | 投稿 AI 初审开关 |

### 权限控制

- 文章管理、构建部署、投稿审核工具：**始终要求管理员权限**
- 投稿（提交/撤回）：**任何人可用**
- 权限判定优先复用 AstrBot 框架的 `event.is_admin()`，其次匹配 `admin_umo`（支持 `platform:user_id` 完整格式或仅用户 ID）
- 未配置 `admin_umo` 时所有管理操作免验证（方便调试）

```yaml
admin_umo: "123456789"        # 推荐：仅用户 ID，匹配任意平台
# admin_umo: "onebot:123456789"  # 限定平台
```

> **安全提示**：生产环境务必配置 `admin_umo`，否则任何人都可执行构建、部署等管理操作。

### AI 初审

开启 `enable_ai_review` 后，投稿提交时自动调用 LLM 评估：内容质量评分（0-10）、优点、问题、改进建议、过审/打回建议。结果持久化，管理员可在投稿详情中查看。关闭后仅发送提醒通知。

## 常见问题

**Q: 构建时内存不足怎么办？**
使用 `remote_build` 模式让远端构建，或调低 `build_memory_threshold`。

**Q: 文章修改后网站没变化？**
Firefly 是静态博客，修改后必须重新构建并部署。

**Q: pnpm 依赖没装？**
`node_modules` 不存在时，构建前先运行 `install_blog_dependencies` 工具（或 `pnpm install`）。

**Q: SSH 断联？**
插件基于 asyncssh，自带 keepalive（30 秒 / 3 次重试），下次操作自动重连。

**Q: 如何启用 HTTPS？**
在 `deploy.conf` 中配置 `DOMAIN_NAME`、`ENABLE_HTTPS=true`、`SSL_CERT_PATH`、`SSL_CERT_KEY_PATH`，脚本自动配置并做 HTTP → HTTPS 重定向。

## 技术细节

- 远程操作基于 asyncssh，异步非阻塞，密码经临时文件传递（不出现在进程列表/命令行）
- 命令注入防护：参数 `shlex.quote()` 转义 + 危险 shell 模式检测
- 构建超时 10 分钟，部署超时 5 分钟；部署优先 rsync，失败回退 scp
- 主机密钥验证默认开启（防中间人）

## 项目结构

```
astrbot_plugin_Firefly_Blog_Manager/
├── main.py / constants.py / models.py / common.py   # 入口、常量、模型、工具
├── executors.py / filesystem.py                      # 命令执行（本地/SSH）、文件系统（本地/SFTP）
├── blog_manager.py / build_deploy.py                 # 文章管理、构建部署
├── webui.py / pages/manager/                         # WebUI 后端与前端页面
├── metadata.yaml / _conf_schema.json                 # 元数据与配置定义
├── requirements.txt
├── deploy.sh / deploy.ps1 / deploy.conf.example      # 独立部署脚本
└── CHANGELOG.md / LICENSE
```

## 开发者

- 作者：月凌
- 仓库：https://github.com/qiyueling2716/astrbot_plugin_Firefly_Blog_Manager
- 许可证：MIT
