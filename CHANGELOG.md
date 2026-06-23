# Changelog

所有显著变更都会记录在此文件。

## [1.3.0] - 2026-06-23

### 新增

- **权限控制功能**：新增 `allow_only_owner` 配置项，开启后仅主人用户可使用插件
- **自定义主人 ID**：新增 `owner_user_id` 配置项，支持自定义主人用户 ID
- **投稿功能**：新增 `submit_post_draft` 工具，任何人都可以提交文章草稿
- **投稿列表**：新增 `list_post_submissions` 工具，查看所有待审核投稿
- **投稿详情**：新增 `review_submission` 工具，查看指定投稿的详细内容
- **批准投稿**：新增 `approve_submission` 工具，批准投稿并发布到博客
- **拒绝投稿**：新增 `reject_submission` 工具，拒绝投稿并可填写拒绝原因

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
