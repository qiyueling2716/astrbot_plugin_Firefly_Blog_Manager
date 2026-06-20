import os
import re
import paramiko
from datetime import datetime
from pathlib import Path
from typing import Optional

from astrbot.api.all import *
from astrbot.api.star import Context, Star, register
from astrbot.api import logger

# 确保 AstrBot 版本兼容
import astrbot
if not hasattr(astrbot, 'version') or astrbot.version < '4.16':
    raise ImportError("astrbot_plugin_Firefly_Blog_Manager 需要 AstrBot >= 4.16 版本")


@register("astrbot_plugin_Firefly_Blog_Manager", "qiyueling2716", "博客远程管理插件", "1.0.0")
class FireflyBlogManagerPlugin(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config
        self.ssh_client = None
        self.connection_type = config.get("connection_type", "local")
        
        # 根据连接类型初始化
        if self.connection_type == "remote":
            self._connect_ssh()

    # ==================== SSH 相关方法 ====================
    def _connect_ssh(self) -> bool:
        """建立 SSH 连接到远程服务器"""
        try:
            self.ssh_client = paramiko.SSHClient()
            self.ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            hostname = self.config.get("server_ip")
            port = self.config.get("server_port", 22)
            username = self.config.get("username")
            auth_type = self.config.get("auth_type", "key")

            if not hostname or not username:
                logger.error("远程连接缺少 server_ip 或 username 配置")
                return False

            connect_kwargs = {
                "hostname": hostname,
                "port": port,
                "username": username,
                "timeout": 10
            }

            if auth_type == "password":
                password = self.config.get("password")
                if not password:
                    logger.error("密码认证方式但未配置 password")
                    return False
                connect_kwargs["password"] = password
                logger.info(f"使用密码认证连接 {hostname}")
            else:
                private_key_path = self.config.get("private_key_path")
                if not private_key_path or not os.path.exists(private_key_path):
                    logger.error(f"私钥文件不存在: {private_key_path}")
                    return False
                private_key = paramiko.RSAKey.from_private_key_file(private_key_path)
                connect_kwargs["pkey"] = private_key
                logger.info(f"使用密钥认证连接 {hostname}")

            self.ssh_client.connect(**connect_kwargs)
            logger.info(f"SSH 连接成功: {hostname}")
            return True
        except paramiko.AuthenticationException:
            logger.error(f"SSH 认证失败，请检查 {'密码' if auth_type == 'password' else '私钥'} 是否正确")
            return False
        except Exception as e:
            logger.error(f"SSH 连接失败: {e}")
            return False

    def _ensure_connection(self) -> bool:
        """确保 SSH 连接有效"""
        if self.connection_type == "local":
            return True
        if self.ssh_client is None:
            return self._connect_ssh()
        try:
            self.ssh_client.exec_command("echo test", timeout=2)
            return True
        except Exception:
            logger.warning("SSH 连接已断开，尝试重连...")
            self.ssh_client = None
            return self._connect_ssh()

    def _execute_command(self, command: str) -> tuple[str, str]:
        """执行命令，本地直接执行或远程 SSH"""
        if self.connection_type == "local":
            import subprocess
            try:
                result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=30)
                return result.stdout.strip(), result.stderr.strip()
            except Exception as e:
                logger.error(f"本地命令执行失败: {command}, 错误: {e}")
                return "", str(e)
        else:
            if not self._ensure_connection():
                return "", "SSH 未连接"
            try:
                stdin, stdout, stderr = self.ssh_client.exec_command(command)
                stdout_str = stdout.read().decode('utf-8').strip()
                stderr_str = stderr.read().decode('utf-8').strip()
                return stdout_str, stderr_str
            except Exception as e:
                logger.error(f"远程命令执行失败: {command}, 错误: {e}")
                return "", str(e)

    def _get_posts_dir(self) -> str:
        """获取文章目录路径"""
        if self.connection_type == "local":
            return os.path.join(self.config.get("local_blog_root", "/var/www/firefly"), "posts")
        else:
            return os.path.join(self.config.get("remote_blog_root", "/var/www/firefly"), "posts")

    def _write_file(self, file_path: str, content: str) -> bool:
        """写入文件，本地或远程"""
        if self.connection_type == "local":
            try:
                os.makedirs(os.path.dirname(file_path), exist_ok=True)
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(content)
                return True
            except Exception as e:
                logger.error(f"本地文件写入失败: {e}")
                return False
        else:
            if not self._ensure_connection():
                return False
            try:
                sftp = self.ssh_client.open_sftp()
                with sftp.open(file_path, 'w') as f:
                    f.write(content)
                sftp.close()
                return True
            except Exception as e:
                logger.error(f"远程文件写入失败: {e}")
                return False

    def _read_file(self, file_path: str) -> Optional[str]:
        """读取文件，本地或远程"""
        if self.connection_type == "local":
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    return f.read()
            except Exception as e:
                logger.error(f"本地文件读取失败: {e}")
                return None
        else:
            if not self._ensure_connection():
                return None
            try:
                sftp = self.ssh_client.open_sftp()
                with sftp.open(file_path, 'r') as f:
                    content = f.read().decode('utf-8')
                sftp.close()
                return content
            except Exception as e:
                logger.error(f"远程文件读取失败: {e}")
                return None

    def _file_exists(self, file_path: str) -> bool:
        """检查文件是否存在，本地或远程"""
        if self.connection_type == "local":
            return os.path.exists(file_path)
        else:
            stdout, _ = self._execute_command(f"test -f {file_path} && echo 'exists'")
            return "exists" in stdout

    def _delete_file(self, file_path: str) -> bool:
        """删除文件，本地或远程"""
        if self.connection_type == "local":
            try:
                os.remove(file_path)
                return True
            except Exception as e:
                logger.error(f"本地文件删除失败: {e}")
                return False
        else:
            stdout, stderr = self._execute_command(f"rm {file_path}")
            return not stderr

    def _list_files(self, pattern: str) -> list:
        """列出文件，本地或远程"""
        if self.connection_type == "local":
            try:
                import glob
                return glob.glob(pattern)
            except Exception as e:
                logger.error(f"本地文件列表失败: {e}")
                return []
        else:
            stdout, _ = self._execute_command(f"ls {pattern} 2>/dev/null")
            if stdout:
                return stdout.split('\n')
            return []

    # ==================== Sanitize 工具方法 ====================
    def _sanitize_filename(self, title: str) -> str:
        """将标题转为安全的文件名"""
        safe = re.sub(r'[^\w\s-]', '', title)
        safe = re.sub(r'[-\s]+', '-', safe)
        return safe.lower()

    def _get_post_frontmatter(self, title: str, tags: list = None) -> str:
        """生成文章的 Frontmatter"""
        if tags is None:
            tags = []
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return f"""---
title: {title}
date: {now}
updated: {now}
tags: {tags}
categories: []
---
"""

    # ==================== Agent 工具方法 ====================
    @agent_tool()
    async def create_post(self, title: str, content: str, tags: str = "") -> str:
        """在博客上创建一篇新文章。"""
        try:
            posts_dir = self._get_posts_dir()
            safe_title = self._sanitize_filename(title)
            filename = f"{safe_title}.md"
            file_path = os.path.join(posts_dir, filename)

            if self._file_exists(file_path):
                return f"❌ 文章《{title}》已存在，请使用其他标题。"

            tags_list = [t.strip() for t in tags.split(',') if t.strip()]
            frontmatter = self._get_post_frontmatter(title, tags_list)
            full_content = frontmatter + "\n" + content

            if self._write_file(file_path, full_content):
                return f"✅ 文章《{title}》创建成功！\n文件路径: {file_path}"
            else:
                return f"❌ 创建文章《{title}》失败，请检查日志。"
        except Exception as e:
            logger.error(f"创建文章失败: {e}")
            return f"❌ 创建文章失败: {str(e)}"

    @agent_tool()
    async def delete_post(self, title_or_filename: str) -> str:
        """删除博客上的一篇文章。"""
        try:
            posts_dir = self._get_posts_dir()
            if title_or_filename.endswith('.md'):
                filename = title_or_filename
            else:
                safe_title = self._sanitize_filename(title_or_filename)
                filename = f"{safe_title}.md"
            file_path = os.path.join(posts_dir, filename)

            if not self._file_exists(file_path):
                return f"❌ 文章《{title_or_filename}》不存在。"

            if self._delete_file(file_path):
                return f"✅ 文章《{title_or_filename}》已删除。"
            else:
                return f"❌ 删除文章《{title_or_filename}》失败。"
        except Exception as e:
            logger.error(f"删除文章失败: {e}")
            return f"❌ 删除失败: {str(e)}"

    @agent_tool()
    async def list_posts(self) -> str:
        """列出博客上的所有文章。"""
        try:
            posts_dir = self._get_posts_dir()
            pattern = os.path.join(posts_dir, "*.md")
            files = self._list_files(pattern)

            if not files:
                return "📭 博客目前没有文章。"

            post_list = []
            for f in files:
                name = os.path.basename(f).replace('.md', '')
                post_list.append(f"• {name}")

            return f"📚 当前共有 {len(post_list)} 篇文章：\n" + "\n".join(post_list)
        except Exception as e:
            logger.error(f"列出文章失败: {e}")
            return f"❌ 获取文章列表失败: {str(e)}"

    @agent_tool()
    async def get_post_content(self, title_or_filename: str) -> str:
        """获取指定文章的完整内容。"""
        try:
            posts_dir = self._get_posts_dir()
            if title_or_filename.endswith('.md'):
                filename = title_or_filename
            else:
                safe_title = self._sanitize_filename(title_or_filename)
                filename = f"{safe_title}.md"
            file_path = os.path.join(posts_dir, filename)

            if not self._file_exists(file_path):
                return f"❌ 文章《{title_or_filename}》不存在。"

            content = self._read_file(file_path)
            if content is None:
                return f"❌ 读取文章《{title_or_filename}》失败。"

            return f"📄 文章《{title_or_filename}》内容：\n\n{content}"
        except Exception as e:
            logger.error(f"获取文章内容失败: {e}")
            return f"❌ 获取文章内容失败: {str(e)}"

    @agent_tool()
    async def update_post(self, title_or_filename: str, content: str, title: str = None) -> str:
        """更新博客上的一篇文章。"""
        try:
            posts_dir = self._get_posts_dir()
            if title_or_filename.endswith('.md'):
                old_filename = title_or_filename
                old_title = old_filename.replace('.md', '')
            else:
                old_title = title_or_filename
                safe_title = self._sanitize_filename(old_title)
                old_filename = f"{safe_title}.md"
            old_file_path = os.path.join(posts_dir, old_filename)

            if not self._file_exists(old_file_path):
                return f"❌ 文章《{old_title}》不存在。"

            if title and title != old_title:
                del_result = await self.delete_post(old_title)
                if "✅" not in del_result:
                    return f"❌ 更新失败: {del_result}"
                return await self.create_post(title, content)

            old_content = self._read_file(old_file_path)
            if old_content is None:
                return f"❌ 读取原文章失败。"

            frontmatter = "---\n"
            if old_content.startswith("---"):
                parts = old_content.split("---", 2)
                if len(parts) >= 3:
                    frontmatter_lines = parts[1].strip().split('\n')
                    new_frontmatter = []
                    has_updated = False
                    for line in frontmatter_lines:
                        if line.startswith("updated:"):
                            new_frontmatter.append(f"updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                            has_updated = True
                        else:
                            new_frontmatter.append(line)
                    if not has_updated:
                        new_frontmatter.append(f"updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                    frontmatter = "---\n" + "\n".join(new_frontmatter) + "\n---\n"

            new_full_content = frontmatter + "\n" + content
            if self._write_file(old_file_path, new_full_content):
                return f"✅ 文章《{old_title}》更新成功！"
            else:
                return f"❌ 更新文章《{old_title}》失败。"
        except Exception as e:
            logger.error(f"更新文章失败: {e}")
            return f"❌ 更新失败: {str(e)}"

    async def terminate(self):
        """插件卸载时关闭 SSH 连接"""
        if self.ssh_client:
            self.ssh_client.close()
            logger.info("SSH 连接已关闭")