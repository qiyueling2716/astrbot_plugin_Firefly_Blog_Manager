# AstrBot Firefly 博客远程管理插件

让 AstrBot 的 Agent 能够通过自然语言指令远程管理 Firefly 博客的文章。

## ✨ 功能特性

- 📝 **创建文章**：通过 AI 指令在远程博客上创建新文章
- 🗑️ **删除文章**：通过 AI 指令删除指定的博客文章
- 📋 **列出文章**：查看博客上所有文章的列表
- 📄 **查看内容**：获取指定文章的完整 Markdown 内容
- ✏️ **更新文章**：更新已有文章的内容或标题

## 📦 安装

### 方法一：通过 AstrBot WebUI 安装（推荐）

1. 打开 AstrBot WebUI → 插件管理
2. 点击「安装插件」
3. 输入插件仓库地址：`https://github.com/qiyueling2716/astrbot_plugin_Firefly_Blog_Manager`
4. 点击安装

### 方法二：手动安装

```bash
cd AstrBot/data/plugins
git clone https://github.com/qiyueling2716/astrbot_plugin_Firefly_Blog_Manager.git
cd astrbot_plugin_Firefly_Blog_Manager
pip install -r requirements.txt
```
然后在 AstrBot WebUI 中重载插件。
## ⚙️ 配置

在 AstrBot WebUI → 插件管理 → 博客远程管理 → 配置，填写以下信息：

配置项 说明 示例
server_ip 运行 Firefly 博客的服务器 IP 123.123.123.123
server_port SSH 端口（默认 22） 22
username SSH 登录用户名 root
auth_type 认证方式：key（密钥）或 password（密码） key
private_key_path 密钥认证时必填，私钥文件绝对路径 /root/.ssh/id_rsa
password 密码认证时必填，SSH 登录密码 ********
blog_root 博客根目录 /var/www/firefly

## 🔑 SSH 认证配置

### 方式一：密钥认证（推荐）

1. 在 AstrBot 所在服务器生成 SSH 密钥（如已有可跳过）：
   ```bash
   ssh-keygen -t rsa -b 4096 -C "astrbot-plugin"
   ```
2. 将公钥添加到旧服务器的 ~/.ssh/authorized_keys：

   ```bash
   ssh-copy-id root@旧服务器IP
   ```
3. 在插件配置中选择 auth_type: key，填写 private_key_path

### 方式二：密码认证

在插件配置中选择 auth_type: password，填写 password

⚠️ 注意：密码认证存在安全风险，建议仅在测试环境使用，生产环境推荐使用密钥认证。

## 🎯 使用示例

在聊天中向 AstrBot 发送以下指令：

创建文章

```
帮我写一篇关于 Docker 部署的博客文章，标题是“Docker 快速入门”
```

删除文章

```
删除文章 Docker 快速入门
```

列出所有文章

```
列出我的博客所有文章
```

查看文章内容

```
查看 Docker 快速入门 这篇文章的内容
```

更新文章

```
把 Docker 快速入门 这篇文章的内容改成...
```

## 🔧 开发者信息

· 作者: 月凌
· 仓库: https://github.com/qiyueling2716/astrbot_plugin_Firefly_Blog_Manager
· 许可证: MIT

## 🛡️ 安全说明

1. 支持 SSH 密钥和密码两种认证方式，推荐使用密钥认证
2. 操作范围被限制在 blog_root/posts/ 目录下
3. 建议通过 AstrBot 权限系统限制高危操作的使用者
4. 所有操作均会记录在 AstrBot 日志中

## 关于Firefly博客部署

### 一键部署脚本
```
curl -fsSL https://raw.githubusercontent.com/qiyueling2716/astrbot_plugin_Firefly_Blog_Manager/master/deploy-firefly.sh | bash
```


## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

## 📄 许可证

MIT License
