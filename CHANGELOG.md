# Changelog

所有显著变更都会记录在此文件。

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
