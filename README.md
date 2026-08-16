# Firefly 博客管理（AstrBot 插件）

让 AstrBot 的 AI 助手帮你用自然语言管理 [Firefly 博客](https://github.com/qiyueling2716/Firefly-Blog)：写文章、审投稿、构建部署，并提供可视化管理页面。

## 功能总览

- **文章管理**：对话即可创建、删除、查找、更新、搜索文章（支持正则搜索），完整支持 YAML Front-matter，编辑/删除前自动备份
- **分类与标签**：AI 可直接查看博客所有分类和标签（按文章数排序），无需翻阅文件
- **投稿系统**：读者直接投稿（支持指定标签和分类），AI 初审评分，管理员审核后发布
- **构建部署**：一条指令或一键按钮完成 环境检查 → 依赖安装 → 构建 → 部署，支持取消和日志回溯
- **进阶语法**：GitHub 卡片、Admonitions、Spoiler、图片画廊、Mermaid、PlantUML、KaTeX 等，可独立开关
- **管理页面**：博客状态、文件管理、站点配置（友链/公告/音乐/留言板/打赏/相册/关于我/RSS）、主题配置、插件配置

## 系统要求

| 组件 | 最低版本 | 说明 |
|------|----------|------|
| AstrBot | >= 4.16 | 插件运行环境 |
| Python | >= 3.10 | 插件依赖 |
| Node.js | >= 22 | 构建博客需要（自动检测） |

> 构建约需 **1.5GB 内存**。服务器内存不足（< 2GB）时请使用 `remote_build` 模式，让远端承担构建。

## 安装

```bash
cd AstrBot/data/plugins
git clone https://github.com/qiyueling2716/astrbot_plugin_Firefly_Blog_Manager.git
cd astrbot_plugin_Firefly_Blog_Manager
pip install -r requirements.txt
```

然后在 AstrBot 面板 → 插件管理中重载插件。也可在面板中按仓库地址直接在线安装。

## 使用方式

### 自然语言（推荐）

直接对 AI 说话即可：*"帮我写一篇 Docker 入门"*、*"删除文章《Docker 快速入门》"*、*"列出所有文章"*。

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

## 管理页面

在 AstrBot 面板「插件管理 → 插件详情 → 页面」中打开，包含五个页签：

- **状态**：部署模式、博客检测结果、构建状态、文章数量、内存状态；「仅构建」与「构建并部署」一键按钮（环境检查 → 依赖安装 → 构建 → 部署，页面实时展示进度）；未检测到博客时可手动指定根目录或重新自动检测
- **站点配置**：博客对外展示内容一站管理：
  - **友链**：条目式行编辑（标题/链接/头像/描述/标签/权重/启用），可任意添加删除
  - **站点信息**：网站标题/副标题/网址/描述/关键词；友链申请页展示信息与申请邮箱
  - **公告 / 音乐 / 留言板**：公告内容；音乐播放来源与歌单；留言板评论系统（Twikoo / Waline / Giscus / Disqus / Artalk / 关闭）
  - **打赏**：标题/描述/使用说明/展示开关，打赏方式（名称/图标/收款码/外链/描述/启用）与打赏者名单（名称/金额/日期）行式增删
  - **相册**：列宽与相册条目（ID/名称/描述/地点/日期/标签/访问密码）行式增删，图片放到博客 `public/gallery/<相册ID>/` 目录
  - **关于我 / RSS**：头像/名字/签名/邮箱与社交链接；RSS 订阅地址由站点信息自动生成
- **文件管理**：树形浏览博客 `src/` 目录，在线编辑/新建/删除/上传，图片等二进制文件内嵌预览；`remote_build` 模式下操作远端仓库
- **主题配置**：博客全部主题配置（6 组分类）可视化编辑——开关/数字/文本/枚举下拉/列表/表格；代码生成的配置自动只读，提示使用文件管理；保存前自动备份 `.bak` 文件
- **插件配置**：插件全部配置项表单化编辑（开关/下拉/数字/文本），保存即时生效；SSH 密码等敏感字段不回显、留空不修改

> 页面会自动从博客壁纸目录随机选取背景图。未检测到已部署的博客时，顶部会提示使用部署脚本初始化。

> 安全：文件操作严格限定在博客 `src/` 目录内；页面与接口由 AstrBot 内置登录鉴权保护。

## 部署模式

| 模式 | 构建位置 | 部署位置 | 适用场景 |
|------|----------|----------|----------|
| `local_build` | AstrBot 服务器 | SSH 推送到远端 | AstrBot 在云服务器，远端是低配 VPS |
| `remote_build` | 远端服务器（SSH） | 远端部署目录 | AstrBot 在本地/低配设备，远端性能更好 |
| `local_only` | AstrBot 服务器 | 同一台机器的部署目录 | 单服务器部署 |

**路径约定**：
- `local_blog_root`：博客**源码**根目录（含 `package.json` 与 `src/content/posts/`），构建的源
- `web_root` / `remote_web_root`：博客**部署目录**（构建产物复制到此，Nginx root 指向这里）

## 一键部署脚本

插件目录的 `deploy.sh`（Linux/macOS）与 `deploy.ps1`（Windows）**独立于 AstrBot 运行**，适合服务器初始化 / CI/CD：自动检测并安装 Python、Node.js（>=22）、pnpm 与依赖，克隆博客仓库、构建部署，可选自动配置 Nginx/Apache、HTTPS、资源监控与备份。

```bash
cp deploy.conf.example deploy.conf   # 编辑填写实际值
chmod +x deploy.sh && ./deploy.sh
```

不创建配置文件时可直接传环境变量（如 `DEPLOY_MODE=local_build LOCAL_BLOG_ROOT=/var/www/firefly ./deploy.sh`）。

### 网络加速（下载慢 / 卡住时）

国内网络环境建议设置 `USE_CN_MIRROR=1`：克隆自动依次尝试内置镜像（每个源有超时），npm/pnpm 自动切换 npmmirror 源；官方源失败时自动重试镜像源。其他可调项：

| 配置项 | 默认 | 说明 |
|--------|------|------|
| `USE_CN_MIRROR` | `0` | 设为 `1` 启用国内镜像加速 |
| `GITHUB_MIRROR` | 空 | 手动指定克隆镜像（如 `https://ghfast.top/https://github.com`） |
| `NPM_REGISTRY` | 空 | 自定义 npm/pnpm 源 |
| `CLONE_DEPTH` | `1` | 浅克隆（完整克隆设 `0`） |
| `GIT_CLONE_TIMEOUT` | `120` | 单个克隆源超时秒数 |
| `REMOTE_CMD_TIMEOUT` | `3600` | 远程构建总超时（remote_build） |
| `AUTO_YES` | `0` | 设为 `1` 跳过所有交互确认（CI 自动生效） |

所有下载、克隆、SSH/rsync 操作均带超时，非交互环境（CI / 后台）不会卡在确认提示上。HTTPS（`ENABLE_HTTPS` + 证书路径）、Web 服务器（`WEB_SERVER=nginx|apache|none`）、资源告警阈值等均可在 `deploy.conf` 中配置，示例见 `deploy.conf.example`。

## SSH 认证

1. **密钥认证（推荐）**：`ssh-keygen -t ed25519` 生成密钥，`ssh-copy-id` 复制公钥到服务器，配置 `auth_type=key` 与 `private_key_path`。请确保服务器允许密钥登录
2. **密码认证**：配置 `auth_type=password` 与 `password` 即可

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
| `admin_umo` | 字符串 | 管理员标识，详见下方"权限控制" |
| `enable_advanced_syntax` | 布尔 | 进阶 Markdown 语法总开关 |
| `advanced_syntax_*` | 布尔 | 8 个子开关（github_card / admonitions / spoiler / image_grid / code_blocks / mermaid / plantuml / katex） |
| `enable_ai_review` | 布尔 | 投稿 AI 初审开关 |

### 权限控制

- 文章管理、构建部署、投稿审核工具：**始终要求管理员权限**
- 投稿（提交/撤回）：**任何人可用**
- 权限判定优先复用 AstrBot 框架的 `event.is_admin()`，其次匹配 `admin_umo`（支持 `platform:user_id` 完整格式或仅用户 ID）

```yaml
admin_umo: "123456789"        # 推荐：仅用户 ID，匹配任意平台
# admin_umo: "onebot:123456789"  # 限定平台
```

> **安全提示**：生产环境务必配置 `admin_umo`，否则任何人都可执行构建、部署等管理操作。

### AI 初审

开启 `enable_ai_review` 后，投稿提交时自动调用大模型评估：内容质量评分（0-10）、优点、问题、改进建议、过审/打回建议。结果持久化，管理员可在投稿详情中查看。关闭后仅发送提醒通知。

## 常见问题

**Q: 构建时内存不足怎么办？**
使用 `remote_build` 模式让远端构建，或调低 `build_memory_threshold`。

**Q: 文章修改后网站没变化？**
Firefly 是静态博客，修改后必须重新构建并部署。

**Q: 站点配置/主题配置改完没生效？**
同上，重新构建并部署即可（管理页「状态」页有一键按钮）。

**Q: pnpm 依赖没装？**
`node_modules` 不存在时，构建前先运行依赖安装（`/博客构建` 会自动检查，或先执行 `install_blog_dependencies` 工具）。

**Q: SSH 断联？**
插件自带保活与自动重连，下次操作自动重连。

**Q: 如何启用 HTTPS？**
在 `deploy.conf` 中配置 `DOMAIN_NAME`、`ENABLE_HTTPS=true`、`SSL_CERT_PATH`、`SSL_CERT_KEY_PATH`，脚本自动配置并做 HTTP → HTTPS 重定向。

## 许可

- 作者：月凌
- 仓库：https://github.com/qiyueling2716/astrbot_plugin_Firefly_Blog_Manager
- 许可证：[GPL-3.0](./LICENSE)
