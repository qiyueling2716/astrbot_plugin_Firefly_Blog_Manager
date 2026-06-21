# Changelog

## [2.0.0] - 2026-06-21

### 重构

- 完全重写核心逻辑，从同步架构迁移到全异步架构
- 引入 `CommandExecutor` / `FileSystem` 抽象层，本地与远程操作统一接口
- 新增 `BuildDeployManager`，将构建和部署流程从文章中独立出来

### 新增

- 三种部署模式：`local_build`、`remote_build`、`local_only`
- 远程 SSH 支持，基于 asyncssh，带 keepalive 和自动重连
- 构建部署工具：`check_blog_environment`、`install_blog_dependencies`、`build_blog`、`deploy_blog`、`build_and_deploy_blog`
- 部署传输优先 rsync，失败自动回退到 scp
- 依赖自动检测，未安装时给出明确提示

### 变更

- 所有 LLM 工具返回值改为 `MessageEventResult`，使用 `yield` 输出结果
- 配置类型改为 `AstrBotConfig`，符合 AstrBot 插件规范
- `_conf_schema.json` 改为扁平 key-value 格式，使用 `options` 替代 `enum`
- 添加 `@register` 装饰器注册插件

### 修复

- 修复静态博客修改后未重新构建部署的问题
- 修复 SSH 断联后无法恢复操作的问题
- 修复构建时内存不足导致进程被杀的问题（通过 remote_build 模式）

---

## [1.0.0] - 早期版本

- 基础文章管理功能（创建、删除、列出、查看、更新、搜索）
