# Changelog

所有显著变更都会记录在此文件。

## [1.3.4] - 2026-06-23

### 修复

- **配置文件清理**：更新 `_conf_schema.json` 和 `README.md`，移除过时的管理员配置描述，明确说明 `owner_user_id` 和 `admin_users` 仅作为回退配置使用
- **文档更新**：更新 README.md 中的权限控制说明，强调插件优先复用 AstrBot 框架的管理员系统

### 改进

- **权限验证逻辑优化**：完善 `_is_admin()` 方法，优先使用框架提供的权限检查方法，只有当框架方法不可用时才回退到配置文件

---

## [1.3.3] - 2026-06-23

### 修复

- **Context.config 属性兼容**：修复新版 AstrBot 框架中 `Context` 对象不再提供 `config` 属性的问题，添加 `hasattr` 检查防止 `AttributeError`
- **装饰器异步兼容性**：修复装饰器不兼容同步返回值的问题，支持异步生成器、同步生成器和单个返回值三种形式
- **SSH 连接状态管理**：增强 `RemoteExecutor` 类，添加 `ConnectionStatus` 枚举、连接状态维护、并发连接保护和 `reset_connection()` 方法
- **权限检查冗余移除**：移除重复的 `@filter.permission_type(filter.PermissionType.ADMIN)` 装饰器，统一使用自定义的 `@require_permission()` 装饰器
- **路径处理一致性**：将 `BlogManager` 中的 `posixpath` 改为 `os.path`，提高跨平台兼容性
- **配置参数校验**：在 `BuildDeployManager.__init__` 中添加部署模式和路径配置的校验逻辑
- **资源检查可移植性**：改进 `_check_system_resources` 方法，添加跨平台磁盘路径选择和更完善的异常处理
- **管理员权限检查**：修复未正确复用 AstrBot 框架管理员列表的问题，优先使用框架提供的 `event.is_admin()`、`event.is_owner()`、`context.is_admin()`、`context.is_owner()` 方法

### 改进

- **日志记录增强**：在文章创建等关键操作中添加详细日志记录，便于问题排查
- **SSH 连接错误处理**：增强连接失败时的状态管理和清理逻辑
- **配置校验增强**：添加 `_validate_path()` 方法校验路径配置的有效性
- **权限系统重构**：新增 `_is_admin()` 方法复用框架权限系统，`_is_admin_from_config()` 作为回退方案

---

## [1.3.2] - 2026-06-23

### 新增

- **管理员列表支持**：新增 `admin_users` 配置项，支持配置多个管理员用户 ID，所有管理员都拥有主人级别的权限
- **多来源管理员提取**：`_get_admin_user_ids()` 方法支持从多个来源提取管理员列表（插件配置 admin_users、插件配置 owner_user_id、AstrBot 全局配置 owner_id、AstrBot 全局配置管理员列表）

### 改进

- `_check_permission` 方法改为支持管理员列表，权限检查逻辑从单个用户 ID 改为用户 ID 集合
- 更新配置文档，添加 `admin_users` 配置说明和管理员来源优先级说明

---

## [1.3.1] - 2026-06-23

### 修复

- **装饰器元数据**：为所有装饰器添加 `functools.wraps`，保留原函数的 `__name__`、`__doc__` 等属性，便于 AstrBot 反射
- **权限检查边界情况**：支持多种用户 ID 字段名（user_id、sender_id、from_id），并统一转为字符串比较避免类型不一致问题
- **文件路径安全**：增强 FilenameUtil.resolve() 方法，防止路径遍历攻击，处理 Windows/Linux 路径分隔符差异
- **投稿持久化**：投稿数据自动保存到 `_submissions_cache.json` 文件，插件重启后投稿数据不丢失
- **terminate 保存投稿**：`terminate()` 方法退出前自动保存投稿缓存，确保数据不丢失
- **auto_setup_blog 重构**：提取 `_do_auto_setup()` 方法，消除重复代码（检查资源→安装依赖→构建）

### 改进

- 投稿提交、批准、拒绝操作后自动持久化保存
- 权限错误消息更加详细，显示当前用户 ID 便于调试
- SSH 连接关闭时有异常处理，避免 terminate 本身失败

---

## [1.3.0] - 2026-06-23

### 新增

- **权限控制功能**：新增 `allow_only_owner` 配置项，开启后仅主人用户可使用插件
- **自定义主人 ID**：新增 `owner_user_id` 配置项，支持自定义主人用户 ID
- **投稿功能**：新增 `submit_post_draft` 工具，任何人都可以提交文章草稿
- **投稿列表**：新增 `list_post_submissions` 工具，查看所有待审核投稿
- **投稿详情**：新增 `review_submission` 工具，查看指定投稿的详细内容
- **批准投稿**：新增 `approve_submission` 工具，批准投稿并发布到博客
- **拒绝投稿**：新增 `reject_submission` 工具，拒绝投稿并可填写拒绝原因
- **显式指令**：新增用户可直接使用的命令，包括 `博客列表`、`博客搜索`、`博客环境`、`博客构建`、`博客部署`、`博客投稿列表`、`内存状态`

### 改进

- 在所有 LLM 工具方法中添加了权限检查逻辑
- 投稿功能无需权限验证，任何人都可以提交
- 投稿审核功能需要主人权限
- 投稿状态管理：支持待审核、已批准、已拒绝三种状态

---

## [1.2.0] - 2026-06-23

### 新增

- **内存资源提醒**：在 README 中添加了构建内存需求说明（约 1.5GB）及缓解方案
- **构建内存阈值配置**：新增 `build_memory_threshold` 配置项（默认 1536MB），可用内存低于此值时自动跳过构建
- **构建内存限制配置**：新增 `build_memory_limit` 配置项，可设置构建内存上限
- **并发构建控制**：新增 `allow_build_concurrent` 配置项，默认禁止并发构建避免内存竞争
- **内存状态检查工具**：新增 `check_memory_status` LLM 工具，查看当前内存状态和构建条件
- **构建资源检查工具**：新增 `check_build_resource` LLM 工具，检查磁盘空间和内存是否充足
- **构建配置查看工具**：新增 `get_build_config` LLM 工具，查看当前构建相关配置

### 改进

- 优化 `build_blog` 方法，构建前自动检查内存是否满足阈值要求
- 更新 `_check_system_resources` 方法，使用配置的内存阈值进行判断
- 优化内存不足时的提示信息，提供更详细的资源信息和解决方案

---

## [1.1.1] - 2026-06-23

### 修复

- **日期格式修复**：修复 YAML Front-matter 中日期字段被加引号的问题。之前 `published` 和 `updated` 字段输出为 `published: '2026-06-23'`（字符串类型），现在正确输出为 `published: 2026-06-23`（纯日期类型），符合 Astro 框架的要求。

### 改进

- 修复 `main.py` 中 `@register` 装饰器的版本号与 `metadata.yaml` 不一致的问题

---

## [1.1.0] - 2026-06-22

### 新增

- **资源监控警告**：构建前检测 CPU/内存使用率，超过阈值时显示警告并询问是否继续
- **构建过程资源日志**：后台记录构建期间的资源使用情况到 `build_resource_monitor.log`
- **Nginx/Apache 自动配置**：一键部署脚本支持自动配置 Web 服务器
- **SSL 证书配置**：支持 HTTPS 配置，自动生成 HTTP 到 HTTPS 重定向
- **域名配置**：支持自定义博客域名配置

### 修复

- **日期格式修复**：修复 `published` 和 `updated` 字段被写成字符串格式的问题，现在正确输出为 YAML 日期类型
- 修复自动检测机制未搜索 `/root` 用户目录的问题
- 修复克隆博客仓库时目标目录已存在导致失败的问题
- 修复 `BuildDeployManager` 跨类调用私有方法导致 `AttributeError` 的问题
- 修正默认部署路径配置为 `/var/www/firefly`

### 改进

- 增强智能检测和路径匹配（支持大小写不敏感）
- 提供更详细的错误信息和诊断提示
- 部署传输优先 rsync，失败自动回退到 scp

---

## [1.0.0] - 2026-06-22

### 新增

- 三种部署模式：`local_build`、`remote_build`、`local_only`
- 远程 SSH 支持，基于 asyncssh，带 keepalive 和自动重连
- 构建部署工具：环境检查、依赖安装、构建、部署
- 一键部署脚本支持 Web 服务器配置（Nginx/Apache）
- SSL 证书配置支持
- 域名配置支持
- 部署传输优先 rsync，失败自动回退到 scp

### 修复

- 修复自动检测机制未搜索 `/root` 用户目录的问题
- 修复克隆博客仓库时目标目录已存在导致失败的问题
- 修复 `BuildDeployManager` 跨类调用私有方法导致 `AttributeError` 的问题
- 修正默认部署路径配置为 `/var/www/firefly`

### 改进

- 增强智能检测和路径匹配
- 提供更详细的错误信息和诊断提示

---

## [0.x.x] - 早期版本

### 重构

- 从同步架构迁移到全异步架构
- 引入 `CommandExecutor` / `FileSystem` 抽象层，统一本地与远程操作接口
- 新增 `BuildDeployManager`，将构建和部署流程独立出来

### 新增

- 三种部署模式：`local_build`、`remote_build`、`local_only`
- 远程 SSH 支持，基于 asyncssh，带 keepalive 和自动重连
- 构建部署工具：`check_blog_environment`、`install_blog_dependencies`、`build_blog`、`deploy_blog`、`build_and_deploy_blog`
- 一键部署脚本 `deploy.sh`（Linux/macOS）和 `deploy.ps1`（Windows）
- 部署脚本自动检测并安装缺失依赖（Python、Node.js、pnpm、pip 包）
- 部署脚本自动克隆 Firefly 博客仓库（支持镜像加速）
- 部署传输优先 rsync，失败自动回退到 scp

### 变更

- 所有 LLM 工具返回值改为 `MessageEventResult`，使用 `yield` 输出结果
- 配置类型改为 `AstrBotConfig`，符合 AstrBot 插件规范
- `_conf_schema.json` 改为扁平 key-value 格式，使用 `options` 替代 `enum`
- 添加 `@register` 装饰器注册插件

### 修复

- 修复静态博客修改后未重新构建部署的问题
- 修复 SSH 断联后无法恢复操作的问题
- 修复构建时内存不足导致进程被杀的问题（通过 remote_build 模式）
- 修复 Windows 上远程路径分隔符兼容性问题

---

## [1.0.0] - 早期版本

- 基础文章管理功能（创建、删除、列出、查看、更新、搜索）
