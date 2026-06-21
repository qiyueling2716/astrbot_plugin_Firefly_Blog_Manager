"""
AstrBot Firefly 博客管理插件 v2.0

通过 AI 指令管理 Firefly 博客的文章和部署。
支持三种部署模式：
1. 本地构建模式 (local_build) - 在 AstrBot 所在服务器构建，通过 SSH 部署到远端
2. 远程构建模式 (remote_build) - 通过 SSH 在远端服务器直接构建部署
3. 纯本地模式 (local_only) - 博客和 AstrBot 在同一台机器

Firefly 博客基于 Astro 框架，文章以 Markdown 文件形式存储，
使用 YAML Front-matter 定义文章元数据。
构建产物位于 dist/ 目录，需部署到 Web 服务器。
"""

from __future__ import annotations

import os
import posixpath
import re
import glob
import shutil
import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

import yaml

from astrbot.api import logger, AstrBotConfig
from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register


# ============================================================================
# 部署模式枚举
# ============================================================================

class DeployMode(str, Enum):
    """部署模式"""
    LOCAL_BUILD = "local_build"      # 本地构建，SSH 部署到远端
    REMOTE_BUILD = "remote_build"    # 远端直接构建
    LOCAL_ONLY = "local_only"        # 纯本地（博客和 AstrBot 在同一台机器）


# ============================================================================
# 数据模型
# ============================================================================

@dataclass
class PostMetadata:
    """Firefly 博客文章元数据（对应 YAML Front-matter）"""
    title: str = ""
    published: str = ""
    updated: str = ""
    description: str = ""
    image: str = ""
    tags: list = field(default_factory=list)
    category: str = ""
    draft: bool = False
    slug: str = ""
    password: str = ""
    password_hint: str = ""
    lang: str = "zh-CN"
    license_name: str = ""
    license_url: str = ""
    author: str = ""
    source_link: str = ""
    comment: bool = True
    pinned: bool = False

    def to_yaml(self) -> str:
        """将元数据转换为 YAML Front-matter 格式"""
        data: dict = {
            "title": self.title,
            "published": self.published,
            "updated": self.updated,
        }
        if self.description:
            data["description"] = self.description
        if self.image:
            data["image"] = self.image
        if self.tags:
            data["tags"] = self.tags
        if self.category:
            data["category"] = self.category
        if self.draft:
            data["draft"] = True
        if self.slug:
            data["slug"] = self.slug
        if self.password:
            data["password"] = self.password
        if self.password_hint:
            data["passwordHint"] = self.password_hint
        if self.lang != "zh-CN":
            data["lang"] = self.lang
        if self.license_name:
            data["licenseName"] = self.license_name
        if self.license_url:
            data["licenseUrl"] = self.license_url
        if self.author:
            data["author"] = self.author
        if self.source_link:
            data["sourceLink"] = self.source_link
        if not self.comment:
            data["comment"] = False
        if self.pinned:
            data["pinned"] = True

        yaml_str = yaml.dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False)
        return f"---\n{yaml_str}---\n"

    @classmethod
    def from_content(cls, content: str) -> tuple[PostMetadata, str]:
        """从文章完整内容中解析元数据和正文，返回 (metadata, body)"""
        metadata = cls()
        body = content

        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                try:
                    data = yaml.safe_load(parts[1])
                    if data and isinstance(data, dict):
                        metadata.title = data.get("title", "")
                        metadata.published = data.get("published", "")
                        metadata.updated = data.get("updated", "")
                        metadata.description = data.get("description", "")
                        metadata.image = data.get("image", "")
                        metadata.tags = data.get("tags", [])
                        metadata.category = data.get("category", "")
                        metadata.draft = data.get("draft", False)
                        metadata.slug = data.get("slug", "")
                        metadata.password = data.get("password", "")
                        metadata.password_hint = data.get("passwordHint", "")
                        metadata.lang = data.get("lang", "zh-CN")
                        metadata.license_name = data.get("licenseName", "")
                        metadata.license_url = data.get("licenseUrl", "")
                        metadata.author = data.get("author", "")
                        metadata.source_link = data.get("sourceLink", "")
                        metadata.comment = data.get("comment", True)
                        metadata.pinned = data.get("pinned", False)
                    body = parts[2].strip()
                except yaml.YAMLError as e:
                    logger.warning(f"解析 Front-matter 失败: {e}")

        return metadata, body


@dataclass
class PostInfo:
    """文章信息摘要（用于列表展示）"""
    filename: str
    title: str
    published: str
    category: str
    tags: list
    draft: bool


# ============================================================================
# 命令执行器抽象
# ============================================================================

class CommandExecutor(ABC):
    """命令执行器抽象基类，统一本地和远程命令执行接口"""

    @abstractmethod
    async def run(self, command: str, cwd: Optional[str] = None, timeout: int = 300) -> tuple[int, str, str]:
        """执行命令，返回 (returncode, stdout, stderr)"""
        pass

    @abstractmethod
    async def close(self):
        """关闭连接/清理资源"""
        pass


class LocalExecutor(CommandExecutor):
    """本地命令执行器，使用 asyncio 子进程"""

    async def run(self, command: str, cwd: Optional[str] = None, timeout: int = 300) -> tuple[int, str, str]:
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
                return (
                    proc.returncode or 0,
                    stdout.decode("utf-8", errors="replace"),
                    stderr.decode("utf-8", errors="replace"),
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return -1, "", f"命令执行超时（{timeout}秒）"
        except Exception as e:
            return -1, "", str(e)

    async def close(self):
        pass


class RemoteExecutor(CommandExecutor):
    """远程 SSH 命令执行器，使用 asyncssh 异步连接，按需建立，带保活和重连"""

    def __init__(self, config: AstrBotConfig):
        self.config = config
        self._conn = None
        self._sftp = None
        self._lock = asyncio.Lock()

    async def _ensure_connected(self):
        """确保 SSH 连接已建立（带锁防止并发连接竞争）"""
        async with self._lock:
            # 如果已有连接，检查是否存活
            if self._conn is not None:
                try:
                    # 发送 keepalive 探测
                    await self._conn.run("echo ok", timeout=5)
                    return
                except Exception:
                    logger.warning("[SSH] 连接已断开，尝试重连")
                    self._conn = None
                    self._sftp = None

            try:
                import asyncssh
            except ImportError:
                raise RuntimeError("远程模式需要 asyncssh 库，请安装: pip install asyncssh")

            hostname = self.config.get("server_ip", "")
            port = self.config.get("server_port", 22)
            username = self.config.get("username", "")
            auth_type = self.config.get("auth_type", "key")

            if not hostname or not username:
                raise ValueError("远程连接缺少 server_ip 或 username 配置")

            connect_options: dict = {
                "host": hostname,
                "port": port,
                "username": username,
                "known_hosts": None,
                "keepalive_interval": 30,
                "keepalive_count_max": 3,
            }

            if auth_type == "password":
                password = self.config.get("password", "")
                if not password:
                    raise ValueError("密码认证方式但未配置 password")
                connect_options["password"] = password
                logger.info(f"[SSH] 使用密码认证连接 {hostname}")
            else:
                key_path = self.config.get("private_key_path", "")
                if key_path and os.path.exists(key_path):
                    connect_options["client_keys"] = [key_path]
                    logger.info(f"[SSH] 使用密钥认证连接 {hostname}")
                else:
                    logger.warning(f"[SSH] 私钥文件不存在: {key_path}，尝试使用 SSH Agent")

            try:
                self._conn = await asyncssh.connect(**connect_options)
                logger.info(f"[SSH] 连接成功: {hostname}")
            except asyncssh.Error as e:
                logger.error(f"[SSH] 连接失败: {e}")
                raise

    async def run(self, command: str, cwd: Optional[str] = None, timeout: int = 300) -> tuple[int, str, str]:
        await self._ensure_connected()

        if cwd:
            command = f"cd {cwd} && {command}"

        try:
            result = await self._conn.run(command, timeout=timeout)
            return result.exit_status or 0, result.stdout or "", result.stderr or ""
        except asyncssh.TimeoutError:
            return -1, "", f"命令执行超时（{timeout}秒）"
        except asyncssh.Error as e:
            return -1, "", str(e)

    async def get_sftp(self):
        """获取 SFTP 客户端（复用已有连接）"""
        await self._ensure_connected()
        if self._sftp is None:
            self._sftp = await self._conn.start_sftp_client()
        return self._sftp

    async def close(self):
        async with self._lock:
            if self._sftp:
                try:
                    self._sftp.exit()
                except Exception:
                    pass
                self._sftp = None
            if self._conn:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None
                logger.info("[SSH] 连接已关闭")


# ============================================================================
# 文件系统操作抽象
# ============================================================================

class FileSystem(ABC):
    """文件系统操作抽象基类"""

    @abstractmethod
    async def list_files(self, pattern: str) -> list[str]:
        pass

    @abstractmethod
    async def read_file(self, path: str) -> Optional[str]:
        pass

    @abstractmethod
    async def write_file(self, path: str, content: str) -> bool:
        pass

    @abstractmethod
    async def delete_file(self, path: str) -> bool:
        pass

    @abstractmethod
    async def exists(self, path: str) -> bool:
        pass


class LocalFileSystem(FileSystem):
    """本地文件系统操作"""

    async def list_files(self, pattern: str) -> list[str]:
        return glob.glob(pattern)

    async def read_file(self, path: str) -> Optional[str]:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            logger.error(f"读取文件失败 {path}: {e}")
            return None

    async def write_file(self, path: str, content: str) -> bool:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            return True
        except Exception as e:
            logger.error(f"写入文件失败 {path}: {e}")
            return False

    async def delete_file(self, path: str) -> bool:
        try:
            if os.path.isdir(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
            return True
        except Exception as e:
            logger.error(f"删除文件失败 {path}: {e}")
            return False

    async def exists(self, path: str) -> bool:
        return os.path.exists(path)


class RemoteFileSystem(FileSystem):
    """远程 SFTP 文件系统操作（复用 RemoteExecutor 的 SSH 连接）"""

    def __init__(self, executor: RemoteExecutor):
        self.executor = executor

    async def list_files(self, pattern: str) -> list[str]:
        try:
            sftp = await self.executor.get_sftp()
            return await sftp.glob(pattern)
        except Exception as e:
            logger.error(f"列出远程文件失败: {e}")
            return []

    async def read_file(self, path: str) -> Optional[str]:
        try:
            sftp = await self.executor.get_sftp()
            async with sftp.open(path, "r") as f:
                return await f.read()
        except Exception as e:
            logger.error(f"读取远程文件失败 {path}: {e}")
            return None

    async def write_file(self, path: str, content: str) -> bool:
        try:
            sftp = await self.executor.get_sftp()
            dir_path = os.path.dirname(path)
            try:
                await sftp.mkdir(dir_path)
            except Exception:
                pass  # 目录已存在
            async with sftp.open(path, "w") as f:
                await f.write(content)
            return True
        except Exception as e:
            logger.error(f"写入远程文件失败 {path}: {e}")
            return False

    async def delete_file(self, path: str) -> bool:
        try:
            sftp = await self.executor.get_sftp()
            try:
                await sftp.remove(path)
            except Exception:
                await self._rmdir_recursive(sftp, path)
            return True
        except Exception as e:
            logger.error(f"删除远程文件失败 {path}: {e}")
            return False

    async def _rmdir_recursive(self, sftp, path: str):
        """递归删除远程目录"""
        try:
            for entry in await sftp.listdir(path):
                child_path = f"{path}/{entry}"
                try:
                    await sftp.remove(child_path)
                except Exception:
                    await self._rmdir_recursive(sftp, child_path)
            await sftp.rmdir(path)
        except Exception as e:
            logger.warning(f"递归删除远程目录失败 {path}: {e}")

    async def exists(self, path: str) -> bool:
        try:
            sftp = await self.executor.get_sftp()
            await sftp.stat(path)
            return True
        except Exception:
            return False


# ============================================================================
# 博客文章管理器
# ============================================================================

class BlogManager:
    """博客文章管理器，封装文章的 CRUD 操作"""

    def __init__(self, fs: FileSystem, posts_dir: str):
        self.fs = fs
        self.posts_dir = posts_dir

    def _make_path(self, filename: str) -> str:
        """构建文章完整路径"""
        return posixpath.join(self.posts_dir, filename)

    async def list_posts(self) -> list[PostInfo]:
        """列出所有文章，返回文章信息列表"""
        pattern = posixpath.join(self.posts_dir, "*.md")
        files = await self.fs.list_files(pattern)
        posts = []
        for filepath in files:
            try:
                content = await self.fs.read_file(filepath)
                if content is None:
                    continue
                metadata, _ = PostMetadata.from_content(content)
                posts.append(PostInfo(
                    filename=os.path.basename(filepath),
                    title=metadata.title or os.path.basename(filepath).replace(".md", ""),
                    published=metadata.published,
                    category=metadata.category,
                    tags=metadata.tags,
                    draft=metadata.draft,
                ))
            except Exception as e:
                logger.warning(f"读取文章失败 {filepath}: {e}")
        return posts

    async def read_post(self, filename: str) -> Optional[str]:
        """读取文章完整内容"""
        return await self.fs.read_file(self._make_path(filename))

    async def write_post(self, filename: str, content: str) -> bool:
        """写入文章"""
        return await self.fs.write_file(self._make_path(filename), content)

    async def delete_post(self, filename: str) -> bool:
        """删除文章"""
        return await self.fs.delete_file(self._make_path(filename))

    async def exists(self, filename: str) -> bool:
        """检查文章是否存在"""
        return await self.fs.exists(self._make_path(filename))


# ============================================================================
# 构建部署管理器
# ============================================================================

class BuildDeployManager:
    """构建和部署管理器，负责博客的构建和部署流程"""

    def __init__(self, config: AstrBotConfig, local_executor: CommandExecutor, remote_executor: Optional[RemoteExecutor] = None):
        self.config = config
        self.local_executor = local_executor
        self.remote_executor = remote_executor
        self.deploy_mode = DeployMode(config.get("deploy_mode", "local_build"))
        self.blog_root = config.get("local_blog_root", "/var/www/firefly")
        self.remote_blog_root = config.get("remote_blog_root", "/var/www/firefly")
        self.web_root = config.get("web_root", "/var/www/html")
        self.remote_web_root = config.get("remote_web_root", "/var/www/html")

    def _get_executor(self) -> CommandExecutor:
        """根据部署模式获取对应的命令执行器"""
        if self.deploy_mode == DeployMode.REMOTE_BUILD and self.remote_executor:
            return self.remote_executor
        return self.local_executor

    def _get_blog_root(self) -> str:
        """根据部署模式获取博客根目录"""
        if self.deploy_mode == DeployMode.REMOTE_BUILD:
            return self.remote_blog_root
        return self.blog_root

    async def check_environment(self) -> tuple[bool, str]:
        """检查构建环境（Node.js 和 pnpm）"""
        executor = self._get_executor()
        rc, out, err = await executor.run("node --version")
        if rc != 0:
            return False, f"未安装 Node.js: {err}"
        rc, out, err = await executor.run("pnpm --version")
        if rc != 0:
            return False, f"未安装 pnpm: {err}"
        return True, f"环境正常，Node.js: {out.strip()}"

    async def install_dependencies(self) -> tuple[bool, str]:
        """安装 pnpm 依赖"""
        executor = self._get_executor()
        blog_root = self._get_blog_root()
        rc, out, err = await executor.run("pnpm install", cwd=blog_root, timeout=300)
        if rc != 0:
            return False, f"依赖安装失败:\n{err}"
        return True, "依赖安装成功"

    async def build(self) -> tuple[bool, str]:
        """执行 pnpm build 构建博客"""
        executor = self._get_executor()
        blog_root = self._get_blog_root()
        rc, out, err = await executor.run("pnpm build", cwd=blog_root, timeout=600)
        if rc != 0:
            return False, f"构建失败:\n{err}"
        return True, "构建成功"

    async def deploy(self) -> tuple[bool, str]:
        """部署构建产物到 Web 服务器"""
        if self.deploy_mode == DeployMode.LOCAL_ONLY:
            return await self._deploy_local()
        elif self.deploy_mode == DeployMode.LOCAL_BUILD:
            return await self._deploy_local_to_remote()
        elif self.deploy_mode == DeployMode.REMOTE_BUILD:
            return await self._deploy_remote()
        return False, "未知的部署模式"

    async def _deploy_local(self):
        """纯本地部署：复制 dist/ 到本地 web 目录"""
        local_dist = os.path.join(self.blog_root, "dist")
        if not os.path.exists(local_dist):
            return False, "构建产物不存在，请先构建"
        try:
            if os.path.exists(self.web_root):
                shutil.rmtree(self.web_root)
            shutil.copytree(local_dist, self.web_root)
            return True, f"已部署到 {self.web_root}"
        except Exception as e:
            return False, f"部署失败: {e}"

    async def _deploy_local_to_remote(self):
        """本地构建后通过 rsync/scp 部署到远端"""
        if not self.remote_executor:
            return False, "本地构建模式需要配置远程 SSH"

        local_dist = os.path.join(self.blog_root, "dist")
        if not os.path.exists(local_dist):
            return False, "本地构建产物不存在，请先构建"

        hostname = self.config.get("server_ip", "")
        username = self.config.get("username", "")
        port = self.config.get("server_port", 22)
        auth_type = self.config.get("auth_type", "key")

        # 优先使用 rsync 部署
        ssh_opts = f"-p {port}"
        if auth_type == "password":
            password = self.config.get("password", "")
            rsync_cmd = (
                f'sshpass -p "{password}" rsync -avz --delete '
                f'-e "ssh {ssh_opts} -o StrictHostKeyChecking=no" '
                f'"{local_dist}/" "{username}@{hostname}:{self.remote_web_root}/"'
            )
        else:
            key_path = self.config.get("private_key_path", "")
            if key_path and os.path.exists(key_path):
                ssh_opts += f" -i {key_path}"
            rsync_cmd = (
                f'rsync -avz --delete '
                f'-e "ssh {ssh_opts} -o StrictHostKeyChecking=no" '
                f'"{local_dist}/" "{username}@{hostname}:{self.remote_web_root}/"'
            )

        rc, out, err = await self.local_executor.run(rsync_cmd, timeout=300)
        if rc != 0:
            # rsync 失败，回退到 scp
            logger.warning(f"rsync 失败，尝试 scp: {err}")
            return await self._deploy_via_scp(local_dist, hostname, username, port, auth_type)

        return True, f"已部署到 {hostname}:{self.remote_web_root}"

    async def _deploy_via_scp(self, local_dist: str, hostname: str, username: str, port: int, auth_type: str) -> tuple[bool, str]:
        """通过 scp 部署（rsync 失败时的回退方案）"""
        ssh_opts = f"-P {port} -o StrictHostKeyChecking=no"

        if auth_type == "password":
            password = self.config.get("password", "")
            await self.remote_executor.run(f"rm -rf {self.remote_web_root}/*")
            scp_cmd = (
                f'sshpass -p "{password}" scp -r {ssh_opts} '
                f'"{local_dist}/*" "{username}@{hostname}:{self.remote_web_root}/"'
            )
        else:
            key_path = self.config.get("private_key_path", "")
            if key_path and os.path.exists(key_path):
                ssh_opts += f" -i {key_path}"
            scp_cmd = (
                f'scp -r {ssh_opts} '
                f'"{local_dist}/*" "{username}@{hostname}:{self.remote_web_root}/"'
            )

        rc, out, err = await self.local_executor.run(scp_cmd, timeout=300)
        if rc != 0:
            return False, f"scp 部署失败:\n{err}"
        return True, f"已通过 scp 部署到 {hostname}:{self.remote_web_root}"

    async def _deploy_remote(self):
        """远端构建后直接复制 dist/ 到远端 web 目录"""
        if not self.remote_executor:
            return False, "远程构建模式需要配置 SSH"

        rc, out, err = await self.remote_executor.run(
            f"rm -rf {self.remote_web_root}/* && cp -r {self.remote_blog_root}/dist/* {self.remote_web_root}/",
            timeout=60,
        )
        if rc != 0:
            return False, f"远端部署失败:\n{err}"
        return True, f"已部署到远端 {self.remote_web_root}"

    async def check_dependencies_installed(self) -> bool:
        """检查 node_modules 是否已安装"""
        blog_root = self._get_blog_root()
        if self.deploy_mode == DeployMode.REMOTE_BUILD and self.remote_executor:
            node_modules_path = posixpath.join(blog_root, "node_modules")
            rc, _, _ = await self.remote_executor.run(f"test -d {node_modules_path}")
            return rc == 0
        else:
            node_modules_path = os.path.join(blog_root, "node_modules")
            return os.path.exists(node_modules_path)


# ============================================================================
# 文件名处理工具
# ============================================================================

class FilenameUtil:
    """文件名处理工具，负责标题到文件名的转换"""

    @staticmethod
    def sanitize(title: str) -> str:
        """将标题转换为安全的文件名（保留中英文、数字、连字符）"""
        safe = re.sub(r'[^\w\s\u4e00-\u9fff-]', '', title)
        safe = re.sub(r'[-\s]+', '-', safe)
        return safe.lower().strip('-')

    @staticmethod
    def resolve(title_or_filename: str) -> str:
        """解析用户输入为文件名，如果已经是 .md 后缀则直接使用"""
        if title_or_filename.endswith(".md"):
            return title_or_filename
        return f"{FilenameUtil.sanitize(title_or_filename)}.md"


# ============================================================================
# 插件主类
# ============================================================================

@register(
    "astrbot_plugin_Firefly_Blog_Manager",
    "月凌",
    "通过 AI 指令管理 Firefly 博客文章和部署",
    "2.0.0",
    "https://github.com/qiyueling2716/astrbot_plugin_Firefly_Blog_Manager",
)
class FireflyBlogManager(Star):
    """Firefly 博客管理插件

    提供 LLM 工具让 Agent 能够管理 Firefly 博客的文章（创建、删除、列出、查看、更新、搜索）
    以及博客的构建部署（环境检查、安装依赖、构建、部署）。
    """

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.local_executor = LocalExecutor()
        self.remote_executor: Optional[RemoteExecutor] = None
        self.fs: Optional[FileSystem] = None
        self.blog_manager: Optional[BlogManager] = None
        self.build_manager: Optional[BuildDeployManager] = None
        self._init_components()

    def _init_components(self):
        """根据配置初始化文件系统、博客管理器和构建部署管理器"""
        deploy_mode = DeployMode(self.config.get("deploy_mode", "local_build"))
        blog_root = self.config.get("local_blog_root", "/var/www/firefly")
        posts_dir = os.path.join(blog_root, "src", "content", "posts")

        if deploy_mode == DeployMode.REMOTE_BUILD:
            # 远程构建模式：文章操作和构建都在远端
            self.remote_executor = RemoteExecutor(self.config)
            self.fs = RemoteFileSystem(self.remote_executor)
            remote_blog_root = self.config.get("remote_blog_root", "/var/www/firefly")
            remote_posts_dir = posixpath.join(remote_blog_root, "src", "content", "posts")
            self.blog_manager = BlogManager(self.fs, remote_posts_dir)
            self.build_manager = BuildDeployManager(
                self.config, self.local_executor, self.remote_executor
            )
        else:
            # 本地构建/纯本地模式：文章操作在本地
            self.fs = LocalFileSystem()
            self.blog_manager = BlogManager(self.fs, posts_dir)
            if deploy_mode == DeployMode.LOCAL_BUILD:
                self.remote_executor = RemoteExecutor(self.config)
                self.build_manager = BuildDeployManager(
                    self.config, self.local_executor, self.remote_executor
                )
            else:
                self.build_manager = BuildDeployManager(
                    self.config, self.local_executor
                )

        logger.info(f"[Firefly] 部署模式: {deploy_mode.value}")

    def _format_post_list(self, posts: list[PostInfo]) -> str:
        """格式化文章列表为可读文本"""
        if not posts:
            return "📭 博客目前没有文章。"

        lines = [f"📚 当前共有 {len(posts)} 篇文章："]
        for post in posts:
            status = "📝" if not post.draft else "🚧"
            tags_str = f" [{', '.join(post.tags)}]" if post.tags else ""
            category_str = f" ({post.category})" if post.category else ""
            lines.append(f"{status} {post.title}{category_str}{tags_str}")

        return "\n".join(lines)

    def _build_post_content(self, title: str, body: str, tags: list, **kwargs) -> str:
        """构建完整文章内容（Front-matter + 正文）"""
        now = datetime.now().strftime("%Y-%m-%d")
        metadata = PostMetadata(
            title=title,
            published=now,
            updated=now,
            tags=tags,
            **kwargs,
        )
        return metadata.to_yaml() + "\n" + body

    # ========================================================================
    # 文章管理 LLM 工具
    # ========================================================================

    @filter.llm_tool(name="create_blog_post")
    async def create_post(
        self,
        event: AstrMessageEvent,
        title: str,
        content: str,
        tags: str = "",
        category: str = "",
        description: str = "",
        draft: bool = False,
    ) -> MessageEventResult:
        '''在 Firefly 博客上创建一篇新文章。

        Args:
            title(string): 文章标题
            content(string): 文章正文内容（Markdown 格式）
            tags(string): 文章标签，多个标签用逗号分隔
            category(string): 文章分类
            description(string): 文章描述/摘要
            draft(boolean): 是否为草稿
        '''
        if not self.blog_manager:
            yield event.plain_result("❌ 博客管理器未初始化")
            return

        filename = FilenameUtil.resolve(title)

        if await self.blog_manager.exists(filename):
            yield event.plain_result(f"❌ 文章《{title}》已存在。")
            return

        tag_list = [t.strip() for t in tags.split(",") if t.strip()]
        extra: dict = {}
        if category:
            extra["category"] = category
        if description:
            extra["description"] = description
        if draft:
            extra["draft"] = True

        full_content = self._build_post_content(title, content, tag_list, **extra)

        if await self.blog_manager.write_post(filename, full_content):
            yield event.plain_result(f"✅ 文章《{title}》创建成功！\n提示：创建文章后需要重新构建部署才能生效。")
        else:
            yield event.plain_result(f"❌ 创建文章《{title}》失败。")

    @filter.llm_tool(name="delete_blog_post")
    async def delete_post(self, event: AstrMessageEvent, title: str) -> MessageEventResult:
        '''删除 Firefly 博客上的一篇文章。

        Args:
            title(string): 文章标题或文件名
        '''
        if not self.blog_manager:
            yield event.plain_result("❌ 博客管理器未初始化")
            return

        filename = FilenameUtil.resolve(title)

        if not await self.blog_manager.exists(filename):
            yield event.plain_result(f"❌ 文章《{title}》不存在。")
            return

        if await self.blog_manager.delete_post(filename):
            yield event.plain_result(f"✅ 文章《{title}》已删除。\n提示：删除后需要重新构建部署才能生效。")
        else:
            yield event.plain_result(f"❌ 删除文章《{title}》失败。")

    @filter.llm_tool(name="list_blog_posts")
    async def list_posts(self, event: AstrMessageEvent) -> MessageEventResult:
        '''列出 Firefly 博客上的所有文章。'''
        if not self.blog_manager:
            yield event.plain_result("❌ 博客管理器未初始化")
            return

        posts = await self.blog_manager.list_posts()
        yield event.plain_result(self._format_post_list(posts))

    @filter.llm_tool(name="get_blog_post")
    async def get_post(self, event: AstrMessageEvent, title: str) -> MessageEventResult:
        '''获取 Firefly 博客上指定文章的完整内容。

        Args:
            title(string): 文章标题或文件名
        '''
        if not self.blog_manager:
            yield event.plain_result("❌ 博客管理器未初始化")
            return

        filename = FilenameUtil.resolve(title)

        if not await self.blog_manager.exists(filename):
            yield event.plain_result(f"❌ 文章《{title}》不存在。")
            return

        content = await self.blog_manager.read_post(filename)
        if content is None:
            yield event.plain_result(f"❌ 读取文章《{title}》失败。")
            return

        yield event.plain_result(f"📄 文章《{title}》内容：\n\n{content}")

    @filter.llm_tool(name="update_blog_post")
    async def update_post(
        self,
        event: AstrMessageEvent,
        title: str,
        new_content: str = "",
        new_title: str = "",
        tags: str = "",
        category: str = "",
        description: str = "",
    ) -> MessageEventResult:
        '''更新 Firefly 博客上的一篇文章。

        Args:
            title(string): 要更新的文章标题或文件名
            new_content(string): 新的文章正文，为空则不修改
            new_title(string): 新的文章标题，为空则不修改
            tags(string): 新的标签，为空则不修改
            category(string): 新的分类，为空则不修改
            description(string): 新的描述，为空则不修改
        '''
        if not self.blog_manager:
            yield event.plain_result("❌ 博客管理器未初始化")
            return

        old_filename = FilenameUtil.resolve(title)

        if not await self.blog_manager.exists(old_filename):
            yield event.plain_result(f"❌ 文章《{title}》不存在。")
            return

        old_content = await self.blog_manager.read_post(old_filename)
        if old_content is None:
            yield event.plain_result(f"❌ 读取文章《{title}》失败。")
            return

        metadata, body = PostMetadata.from_content(old_content)

        # 处理标题变更（需要重命名文件）
        if new_title and new_title != metadata.title:
            new_filename = FilenameUtil.resolve(new_title)
            if await self.blog_manager.exists(new_filename):
                yield event.plain_result(f"❌ 目标文章《{new_title}》已存在。")
                return

            metadata.title = new_title
            metadata.updated = datetime.now().strftime("%Y-%m-%d")
            new_full_content = metadata.to_yaml() + "\n" + (new_content or body)

            if await self.blog_manager.write_post(new_filename, new_full_content):
                await self.blog_manager.delete_post(old_filename)
                yield event.plain_result(f"✅ 文章已重命名为《{new_title}》。\n提示：需要重新构建部署才能生效。")
            else:
                yield event.plain_result("❌ 更新文章失败。")
            return

        # 仅更新内容/元数据
        if new_content:
            body = new_content
        if tags:
            metadata.tags = [t.strip() for t in tags.split(",") if t.strip()]
        if category:
            metadata.category = category
        if description:
            metadata.description = description

        metadata.updated = datetime.now().strftime("%Y-%m-%d")
        full_content = metadata.to_yaml() + "\n" + body

        if await self.blog_manager.write_post(old_filename, full_content):
            yield event.plain_result(f"✅ 文章《{metadata.title}》更新成功！\n提示：需要重新构建部署才能生效。")
        else:
            yield event.plain_result(f"❌ 更新文章《{metadata.title}》失败。")

    @filter.llm_tool(name="search_blog_posts")
    async def search_posts(self, event: AstrMessageEvent, keyword: str) -> MessageEventResult:
        '''在 Firefly 博客中搜索文章。

        Args:
            keyword(string): 搜索关键词
        '''
        if not self.blog_manager:
            yield event.plain_result("❌ 博客管理器未初始化")
            return

        posts = await self.blog_manager.list_posts()
        results = []
        keyword_lower = keyword.lower()

        for post in posts:
            if (keyword_lower in post.title.lower()
                    or keyword_lower in post.category.lower()
                    or any(keyword_lower in t.lower() for t in post.tags)):
                results.append(post)

        if not results:
            yield event.plain_result(f"🔍 未找到包含「{keyword}」的文章。")
        else:
            yield event.plain_result(self._format_post_list(results))

    # ========================================================================
    # 构建部署 LLM 工具
    # ========================================================================

    @filter.llm_tool(name="check_blog_environment")
    async def check_environment(self, event: AstrMessageEvent) -> MessageEventResult:
        '''检查 Firefly 博客的构建环境是否就绪（Node.js 和 pnpm）。'''
        if not self.build_manager:
            yield event.plain_result("❌ 构建管理器未初始化")
            return

        ok, msg = await self.build_manager.check_environment()
        if ok:
            yield event.plain_result(f"✅ {msg}")
        else:
            yield event.plain_result(f"❌ {msg}")

    @filter.llm_tool(name="install_blog_dependencies")
    async def install_dependencies(self, event: AstrMessageEvent) -> MessageEventResult:
        '''安装 Firefly 博客的依赖（执行 pnpm install）。'''
        if not self.build_manager:
            yield event.plain_result("❌ 构建管理器未初始化")
            return

        ok, msg = await self.build_manager.install_dependencies()
        if ok:
            yield event.plain_result(f"✅ {msg}")
        else:
            yield event.plain_result(f"❌ {msg}")

    @filter.llm_tool(name="build_blog")
    async def build_blog(self, event: AstrMessageEvent) -> MessageEventResult:
        '''构建 Firefly 博客（执行 pnpm build）。构建可能需要较长时间。'''
        if not self.build_manager:
            yield event.plain_result("❌ 构建管理器未初始化")
            return

        # 检查环境
        ok, msg = await self.build_manager.check_environment()
        if not ok:
            yield event.plain_result(f"❌ 环境检查失败: {msg}\n请先安装 Node.js 和 pnpm。")
            return

        # 检查依赖
        if not await self.build_manager.check_dependencies_installed():
            yield event.plain_result("⚠️ 依赖未安装，请先执行 install_blog_dependencies。")
            return

        ok, msg = await self.build_manager.build()
        if ok:
            yield event.plain_result(f"✅ {msg}\n构建产物位于 dist/ 目录。")
        else:
            yield event.plain_result(f"❌ {msg}")

    @filter.llm_tool(name="deploy_blog")
    async def deploy_blog(self, event: AstrMessageEvent) -> MessageEventResult:
        '''部署 Firefly 博客到 Web 服务器。将构建产物部署到配置的 Web 根目录。'''
        if not self.build_manager:
            yield event.plain_result("❌ 构建管理器未初始化")
            return

        ok, msg = await self.build_manager.deploy()
        if ok:
            yield event.plain_result(f"✅ {msg}")
        else:
            yield event.plain_result(f"❌ {msg}")

    @filter.llm_tool(name="build_and_deploy_blog")
    async def build_and_deploy(self, event: AstrMessageEvent) -> MessageEventResult:
        '''一键构建并部署 Firefly 博客。自动执行：检查环境 -> 安装依赖 -> 构建 -> 部署。'''
        if not self.build_manager:
            yield event.plain_result("❌ 构建管理器未初始化")
            return

        results = []

        # 检查环境
        ok, msg = await self.build_manager.check_environment()
        if not ok:
            yield event.plain_result(f"❌ 环境检查失败: {msg}")
            return
        results.append(f"✅ 环境检查: {msg}")

        # 检查依赖，未安装则自动安装
        if not await self.build_manager.check_dependencies_installed():
            ok, msg = await self.build_manager.install_dependencies()
            if not ok:
                yield event.plain_result(f"❌ 依赖安装失败: {msg}")
                return
            results.append(f"✅ 依赖安装: {msg}")

        # 构建
        ok, msg = await self.build_manager.build()
        if not ok:
            yield event.plain_result(f"❌ 构建失败: {msg}")
            return
        results.append(f"✅ 构建: {msg}")

        # 部署
        ok, msg = await self.build_manager.deploy()
        if not ok:
            yield event.plain_result(f"❌ 部署失败: {msg}")
            return
        results.append(f"✅ 部署: {msg}")

        yield event.plain_result("\n".join(results))

    # ========================================================================
    # 生命周期管理
    # ========================================================================

    async def terminate(self):
        """插件卸载/停用时清理资源（关闭 SSH 连接等）"""
        if self.remote_executor:
            await self.remote_executor.close()
            self.remote_executor = None
        logger.info("[Firefly] 插件资源已清理")
