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

架构说明：
- 采用分层抽象设计，命令执行器和文件系统都有本地/远程两种实现
- 通过依赖注入实现模式切换，核心逻辑与具体实现解耦
- 所有工具函数均支持异步操作，避免阻塞主线程
- 配置采用声明式定义，支持 WebUI 可视化配置
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
from typing import Optional, Dict, List, Any, Tuple, Union

import yaml

from astrbot.api import logger, AstrBotConfig
from astrbot.api.event import filter
from astrbot.api.star import Context, Star, register


# ============================================================================
# 常量定义
# ============================================================================

DEFAULT_BLOG_ROOT = "/var/www/firefly"
DEFAULT_WEB_ROOT = "/var/www/html"
DEFAULT_SSH_PORT = 22
BUILD_TIMEOUT = 600  # 10分钟
DEPLOY_TIMEOUT = 300  # 5分钟
SSH_KEEPALIVE_INTERVAL = 30
SSH_KEEPALIVE_COUNT_MAX = 3


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
# 自定义异常类
# ============================================================================

class BlogManagerError(Exception):
    """博客管理器基础异常"""
    pass


class ConfigurationError(BlogManagerError):
    """配置错误"""
    pass


class BlogNotFoundError(BlogManagerError):
    """博客目录未找到"""
    pass


class PostNotFoundError(BlogManagerError):
    """文章未找到"""
    pass


class BuildError(BlogManagerError):
    """构建错误"""
    pass


class DeployError(BlogManagerError):
    """部署错误"""
    pass


class SSHConnectionError(BlogManagerError):
    """SSH 连接错误"""
    pass


# ============================================================================
# 工具函数
# ============================================================================

def validate_config(config: Dict[str, Any]) -> List[str]:
    """验证配置参数，返回错误信息列表"""
    errors = []
    
    deploy_mode = config.get("deploy_mode", "")
    if deploy_mode not in ["local_only", "local_build", "remote_build"]:
        errors.append(f"无效的部署模式: {deploy_mode}")
    
    if deploy_mode in ["local_build", "remote_build"]:
        if not config.get("server_ip"):
            errors.append("远程部署模式需要配置服务器 IP")
        if not config.get("username"):
            errors.append("远程部署模式需要配置用户名")
    
    auth_type = config.get("auth_type", "key")
    if auth_type not in ["key", "password"]:
        errors.append(f"无效的认证类型: {auth_type}")
    
    if auth_type == "key" and not config.get("private_key_path"):
        errors.append("密钥认证需要配置私钥路径")
    
    return errors


def sanitize_filename(title: str) -> str:
    """将标题转换为安全的文件名"""
    # 移除特殊字符
    filename = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', title)
    # 移除多个连续下划线
    filename = re.sub(r'_+', '_', filename)
    # 移除首尾下划线
    filename = filename.strip('_')
    # 如果为空，使用默认名称
    if not filename:
        filename = "untitled"
    return filename


def ensure_directory_exists(path: str) -> bool:
    """确保目录存在，不存在则创建"""
    try:
        os.makedirs(path, exist_ok=True)
        return True
    except Exception as e:
        logger.error(f"创建目录失败 {path}: {e}")
        return False


def generate_slug(title: str) -> str:
    """从标题生成 URL 友好的 slug"""
    slug = title.lower()
    # 替换空格和特殊字符
    slug = re.sub(r'[\s_]+', '-', slug)
    # 移除非字母数字字符
    slug = re.sub(r'[^a-z0-9\u4e00-\u9fff-]', '', slug)
    # 移除连续的连字符
    slug = re.sub(r'-+', '-', slug)
    # 移除首尾连字符
    slug = slug.strip('-')
    return slug


def format_datetime(dt: datetime) -> str:
    """格式化日期时间为 ISO 格式"""
    return dt.strftime("%Y-%m-%dT%H:%M:%S+08:00")


def parse_datetime(date_str: str) -> Optional[datetime]:
    """解析日期时间字符串"""
    formats = [
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    return None


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
        """执行命令，返回 (returncode, stdout, stderr)"""
        logger.debug(f"[LocalExecutor] 执行命令: {command}")
        
        try:
            # 验证工作目录
            if cwd and not os.path.isdir(cwd):
                return -1, "", f"工作目录不存在: {cwd}"

            proc = await asyncio.create_subprocess_shell(
                command,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=os.environ.copy(),
            )
            
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
                returncode = proc.returncode or 0
                
                if returncode != 0:
                    logger.debug(f"[LocalExecutor] 命令执行失败: {command}, 返回码: {returncode}, 错误: {stderr.decode('utf-8', errors='replace')}")
                
                return (
                    returncode,
                    stdout.decode("utf-8", errors="replace"),
                    stderr.decode("utf-8", errors="replace"),
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                logger.error(f"[LocalExecutor] 命令执行超时: {command}")
                return -1, "", f"命令执行超时（{timeout}秒）"
        except PermissionError:
            logger.error(f"[LocalExecutor] 权限不足: {command}")
            return -1, "", "权限不足，请检查文件或目录权限"
        except FileNotFoundError:
            logger.error(f"[LocalExecutor] 命令未找到: {command}")
            return -1, "", "命令未找到，请检查是否安装了相关工具"
        except Exception as e:
            logger.error(f"[LocalExecutor] 执行命令异常: {command}, 错误: {e}")
            return -1, "", str(e)

    async def close(self):
        """关闭执行器（本地执行器无需特殊清理）"""
        pass


class RemoteExecutor(CommandExecutor):
    """远程 SSH 命令执行器，使用 asyncssh 异步连接，按需建立，带保活和重连"""

    def __init__(self, config: AstrBotConfig):
        self.config = config
        self._conn = None
        self._sftp = None
        self._lock = asyncio.Lock()
        self._connect_attempts = 0
        self._max_connect_attempts = 3

    async def _ensure_connected(self, retry: int = 0):
        """确保 SSH 连接已建立（带锁防止并发连接竞争，支持重试）"""
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
            port = self.config.get("server_port", DEFAULT_SSH_PORT)
            username = self.config.get("username", "")
            auth_type = self.config.get("auth_type", "key")

            if not hostname:
                raise ConfigurationError("缺少 server_ip 配置")
            if not username:
                raise ConfigurationError("缺少 username 配置")

            connect_options: dict = {
                "host": hostname,
                "port": port,
                "username": username,
                "known_hosts": None,
                "keepalive_interval": SSH_KEEPALIVE_INTERVAL,
                "keepalive_count_max": SSH_KEEPALIVE_COUNT_MAX,
                "connect_timeout": 15,
            }

            if auth_type == "password":
                password = self.config.get("password", "")
                if not password:
                    raise ConfigurationError("密码认证方式但未配置 password")
                connect_options["password"] = password
                logger.info(f"[SSH] 使用密码认证连接 {hostname}:{port}")
            else:
                key_path = self.config.get("private_key_path", "")
                if key_path and os.path.exists(key_path):
                    connect_options["client_keys"] = [key_path]
                    logger.info(f"[SSH] 使用密钥认证连接 {hostname}:{port}")
                else:
                    logger.warning(f"[SSH] 私钥文件不存在: {key_path}，尝试使用 SSH Agent")

            try:
                self._conn = await asyncssh.connect(**connect_options)
                self._connect_attempts = 0
                logger.info(f"[SSH] 连接成功: {hostname}:{port}")
            except asyncssh.Error as e:
                self._connect_attempts += 1
                logger.error(f"[SSH] 连接失败 (第 {self._connect_attempts} 次): {e}")
                
                if self._connect_attempts < self._max_connect_attempts:
                    wait_time = 2 ** self._connect_attempts  # 指数退避
                    logger.info(f"[SSH] {wait_time} 秒后重试...")
                    await asyncio.sleep(wait_time)
                    await self._ensure_connected(retry + 1)
                else:
                    raise SSHConnectionError(f"SSH 连接失败，已重试 {self._max_connect_attempts} 次: {e}")

    async def run(self, command: str, cwd: Optional[str] = None, timeout: int = 300) -> tuple[int, str, str]:
        """执行远程命令，返回 (returncode, stdout, stderr)"""
        logger.debug(f"[RemoteExecutor] 执行命令: {command}")
        
        try:
            await self._ensure_connected()

            if cwd:
                command = f"cd {cwd} && {command}"

            try:
                result = await self._conn.run(command, timeout=timeout)
                returncode = result.exit_status or 0
                
                if returncode != 0:
                    logger.debug(f"[RemoteExecutor] 命令执行失败: {command}, 返回码: {returncode}")
                
                return returncode, result.stdout or "", result.stderr or ""
            except asyncssh.TimeoutError:
                logger.error(f"[RemoteExecutor] 命令执行超时: {command}")
                return -1, "", f"命令执行超时（{timeout}秒）"
            except asyncssh.Error as e:
                logger.error(f"[RemoteExecutor] SSH 错误: {command}, 错误: {e}")
                # 重置连接，下次自动重连
                self._conn = None
                self._sftp = None
                return -1, "", str(e)
        except SSHConnectionError as e:
            return -1, "", str(e)
        except ConfigurationError as e:
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
        """列出匹配模式的文件"""
        try:
            files = glob.glob(pattern)
            logger.debug(f"[LocalFileSystem] 列出文件: {pattern}, 找到 {len(files)} 个")
            return files
        except Exception as e:
            logger.error(f"[LocalFileSystem] 列出文件失败 {pattern}: {e}")
            return []

    async def read_file(self, path: str) -> Optional[str]:
        """读取文件内容"""
        logger.debug(f"[LocalFileSystem] 读取文件: {path}")
        
        try:
            if not os.path.exists(path):
                logger.warning(f"[LocalFileSystem] 文件不存在: {path}")
                return None
            
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except PermissionError:
            logger.error(f"[LocalFileSystem] 读取文件权限不足: {path}")
            return None
        except UnicodeDecodeError:
            logger.error(f"[LocalFileSystem] 文件编码错误: {path}")
            return None
        except Exception as e:
            logger.error(f"[LocalFileSystem] 读取文件失败 {path}: {e}")
            return None

    async def write_file(self, path: str, content: str) -> bool:
        """写入文件内容"""
        logger.debug(f"[LocalFileSystem] 写入文件: {path}")
        
        try:
            # 确保目录存在
            dir_path = os.path.dirname(path)
            if dir_path and not os.path.exists(dir_path):
                os.makedirs(dir_path, exist_ok=True)
            
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            return True
        except PermissionError:
            logger.error(f"[LocalFileSystem] 写入文件权限不足: {path}")
            return False
        except Exception as e:
            logger.error(f"[LocalFileSystem] 写入文件失败 {path}: {e}")
            return False

    async def delete_file(self, path: str) -> bool:
        """删除文件或目录"""
        logger.debug(f"[LocalFileSystem] 删除文件: {path}")
        
        try:
            if not os.path.exists(path):
                logger.warning(f"[LocalFileSystem] 文件不存在: {path}")
                return True
            
            if os.path.isdir(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
            return True
        except PermissionError:
            logger.error(f"[LocalFileSystem] 删除文件权限不足: {path}")
            return False
        except Exception as e:
            logger.error(f"[LocalFileSystem] 删除文件失败 {path}: {e}")
            return False

    async def exists(self, path: str) -> bool:
        """检查文件或目录是否存在"""
        return os.path.exists(path)


class RemoteFileSystem(FileSystem):
    """远程 SFTP 文件系统操作（复用 RemoteExecutor 的 SSH 连接）"""

    def __init__(self, executor: RemoteExecutor):
        self.executor = executor

    async def list_files(self, pattern: str) -> list[str]:
        """列出匹配模式的远程文件"""
        logger.debug(f"[RemoteFileSystem] 列出文件: {pattern}")
        
        try:
            sftp = await self.executor.get_sftp()
            files = await sftp.glob(pattern)
            logger.debug(f"[RemoteFileSystem] 找到 {len(files)} 个文件")
            return files
        except Exception as e:
            logger.error(f"[RemoteFileSystem] 列出远程文件失败: {e}")
            return []

    async def read_file(self, path: str) -> Optional[str]:
        """读取远程文件内容"""
        logger.debug(f"[RemoteFileSystem] 读取文件: {path}")
        
        try:
            sftp = await self.executor.get_sftp()
            async with sftp.open(path, "r") as f:
                content = await f.read()
                return content
        except FileNotFoundError:
            logger.warning(f"[RemoteFileSystem] 远程文件不存在: {path}")
            return None
        except PermissionError:
            logger.error(f"[RemoteFileSystem] 读取远程文件权限不足: {path}")
            return None
        except Exception as e:
            logger.error(f"[RemoteFileSystem] 读取远程文件失败 {path}: {e}")
            return None

    async def write_file(self, path: str, content: str) -> bool:
        """写入远程文件内容"""
        logger.debug(f"[RemoteFileSystem] 写入文件: {path}")
        
        try:
            sftp = await self.executor.get_sftp()
            dir_path = posixpath.dirname(path)
            
            # 递归创建目录
            if dir_path and dir_path != "/":
                await self._mkdir_recursive(sftp, dir_path)
            
            async with sftp.open(path, "w") as f:
                await f.write(content)
            return True
        except PermissionError:
            logger.error(f"[RemoteFileSystem] 写入远程文件权限不足: {path}")
            return False
        except Exception as e:
            logger.error(f"[RemoteFileSystem] 写入远程文件失败 {path}: {e}")
            return False

    async def delete_file(self, path: str) -> bool:
        """删除远程文件或目录"""
        logger.debug(f"[RemoteFileSystem] 删除文件: {path}")
        
        try:
            sftp = await self.executor.get_sftp()
            
            # 先检查是否存在
            try:
                await sftp.stat(path)
            except FileNotFoundError:
                logger.warning(f"[RemoteFileSystem] 远程文件不存在: {path}")
                return True
            
            try:
                await sftp.remove(path)
            except Exception:
                await self._rmdir_recursive(sftp, path)
            return True
        except PermissionError:
            logger.error(f"[RemoteFileSystem] 删除远程文件权限不足: {path}")
            return False
        except Exception as e:
            logger.error(f"[RemoteFileSystem] 删除远程文件失败 {path}: {e}")
            return False

    async def _mkdir_recursive(self, sftp, path: str):
        """递归创建远程目录"""
        parts = path.split("/")
        current = ""
        for part in parts:
            if part:
                current += "/" + part
                try:
                    await sftp.stat(current)
                except FileNotFoundError:
                    await sftp.mkdir(current)

    async def _rmdir_recursive(self, sftp, path: str):
        """递归删除远程目录"""
        try:
            async for entry in sftp.listdir(path):
                entry_path = posixpath.join(path, entry)
                try:
                    await sftp.remove(entry_path)
                except Exception:
                    await self._rmdir_recursive(sftp, entry_path)
            await sftp.rmdir(path)
        except Exception as e:
            logger.error(f"[RemoteFileSystem] 递归删除目录失败 {path}: {e}")

    async def exists(self, path: str) -> bool:
        """检查远程文件或目录是否存在"""
        try:
            sftp = await self.executor.get_sftp()
            await sftp.stat(path)
            return True
        except FileNotFoundError:
            return False
        except Exception as e:
            logger.error(f"[RemoteFileSystem] 检查文件存在失败 {path}: {e}")
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
        """安装 pnpm 依赖（如果博客目录不存在则自动克隆）"""
        executor = self._get_executor()
        blog_root = self._get_blog_root()
        
        logger.info(f"[Build] 开始安装依赖，目录: {blog_root}")
        
        # 先检查博客目录是否存在，不存在则自动克隆
        blog_exists = True
        if self.deploy_mode == DeployMode.REMOTE_BUILD:
            rc, out, err = await executor.run(f"ls -la {blog_root}", timeout=10)
            if rc != 0:
                blog_exists = False
        else:
            if not os.path.exists(blog_root):
                blog_exists = False
        
        if not blog_exists:
            msg = await self._clone_blog_repo()
            if not msg.startswith("✅"):
                return False, msg
        
        # 检查 package.json 是否存在，不存在则尝试克隆
        package_json_path = os.path.join(blog_root, "package.json")
        package_exists = True
        if self.deploy_mode == DeployMode.REMOTE_BUILD:
            rc, out, err = await executor.run(f"ls -la {package_json_path}", timeout=10)
            if rc != 0:
                package_exists = False
        else:
            if not os.path.exists(package_json_path):
                package_exists = False
        
        if not package_exists:
            msg = await self._clone_blog_repo()
            if not msg.startswith("✅"):
                return False, msg
        
        rc, out, err = await executor.run("pnpm install", cwd=blog_root, timeout=300)
        
        if rc != 0:
            error_details = []
            
            # 添加基本调试信息
            error_details.append(f"命令执行失败，返回码: {rc}")
            error_details.append(f"工作目录: {blog_root}")
            
            if err:
                # 提取关键错误信息
                error_lines = err.strip().split('\n')
                # 只显示最后几行关键错误
                if len(error_lines) > 10:
                    error_lines = error_lines[-10:]
                error_details.append("\n详细错误信息:")
                error_details.extend(error_lines)
            elif out:
                # 如果没有错误输出但有标准输出，也显示
                error_details.append("\n命令输出:")
                out_lines = out.strip().split('\n')
                if len(out_lines) > 10:
                    out_lines = out_lines[-10:]
                error_details.extend(out_lines)
            else:
                # 完全没有输出的情况
                error_details.append("\n命令无输出，可能的原因:")
                error_details.append("- pnpm 命令不存在或路径问题")
                error_details.append("- 权限不足")
                error_details.append("- 进程被意外终止")
            
            # 检查是否有警告信息
            if out:
                out_lines = out.strip().split('\n')
                warning_lines = [line for line in out_lines if 'WARN' in line or 'warning' in line.lower()]
                if warning_lines:
                    error_details.append("\n警告信息:")
                    error_details.extend(warning_lines[:5])
            
            error_summary = "\n".join(error_details)
            
            # 添加可能的解决方案
            solutions = [
                "\n\n可能的解决方案:",
                "1. 检查网络连接是否正常",
                "2. 尝试设置 npm/pnpm 镜像源:",
                "   pnpm config set registry https://registry.npmmirror.com",
                "3. 检查磁盘空间是否充足",
                "4. 尝试删除 node_modules 和 pnpm-lock.yaml 后重新安装",
                "5. 检查 Node.js 版本 >= 22",
                "6. 检查是否有权限访问博客目录",
                "7. 尝试手动运行 pnpm install 查看详细错误",
            ]
            
            return False, f"{error_summary}{''.join(solutions)}"
        
        # 检查输出中是否有警告
        if out and ('WARN' in out or 'warning' in out.lower()):
            return True, f"依赖安装成功（有警告）\n{out[-500:]}"
        
        return True, "依赖安装成功"

    async def _clone_blog_repo(self) -> str:
        """克隆 Firefly 博客仓库"""
        executor = self._get_executor()
        blog_root = self._get_blog_root()
        
        logger.info(f"[Build] 克隆 Firefly 博客仓库到: {blog_root}")
        
        # GitHub 仓库地址和镜像
        repo_url = "https://github.com/qiyueling2716/Firefly-Blog.git"
        mirror_url = "https://hubproxy.jiaozi.live/https://github.com/qiyueling2716/Firefly-Blog.git"
        
        # 先检查 git 是否安装
        rc, out, err = await executor.run("git --version", timeout=10)
        if rc != 0:
            return "❌ 未安装 git，请先安装 git\n\n解决方案:\n- Ubuntu/Debian: sudo apt install git\n- CentOS/RHEL: sudo yum install git\n- macOS: brew install git\n- Windows: 下载安装 git"
        
        # 创建父目录
        if self.deploy_mode == DeployMode.REMOTE_BUILD:
            parent_dir = os.path.dirname(blog_root)
            await executor.run(f"mkdir -p {parent_dir}", timeout=10)
        else:
            parent_dir = os.path.dirname(blog_root)
            if parent_dir:
                os.makedirs(parent_dir, exist_ok=True)
        
        # 尝试克隆仓库
        rc, out, err = await executor.run(f"git clone {repo_url} {blog_root}", timeout=120)
        if rc != 0:
            logger.warning(f"[Build] 主仓库克隆失败，尝试镜像: {err}")
            rc, out, err = await executor.run(f"git clone {mirror_url} {blog_root}", timeout=120)
            if rc != 0:
                return f"❌ 克隆博客仓库失败\n错误信息: {err}\n\n可能的解决方案:\n1. 检查网络连接\n2. 尝试手动克隆: git clone {repo_url} {blog_root}\n3. 检查目标目录是否有写入权限"
        
        return f"✅ 成功克隆 Firefly 博客仓库到 {blog_root}"

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

    def _is_firefly_blog(self, path: str) -> bool:
        """检查路径是否为 Firefly 博客项目"""
        if not os.path.isdir(path):
            return False
        
        # 检查 Firefly 博客特征文件
        package_json = os.path.join(path, "package.json")
        src_content = os.path.join(path, "src", "content", "posts")
        astro_config = os.path.join(path, "astro.config.mjs")
        
        # 至少满足两个条件
        matches = 0
        if os.path.isfile(package_json):
            matches += 1
        if os.path.isdir(src_content):
            matches += 1
        if os.path.isfile(astro_config):
            matches += 1
        
        return matches >= 2

    def _is_blog_built(self, path: str) -> bool:
        """检查博客是否已构建（存在 dist 目录）"""
        dist_dir = os.path.join(path, "dist")
        return os.path.isdir(dist_dir)

    def _check_system_resources(self) -> tuple[bool, str]:
        """检查系统资源是否足以构建博客"""
        # 检查磁盘空间（至少需要 500MB）
        disk_usage = shutil.disk_usage("/")
        free_space_gb = disk_usage.free / (1024 ** 3)
        if free_space_gb < 0.5:
            return False, f"磁盘空间不足，仅剩余 {free_space_gb:.2f} GB，建议至少 500MB"
        
        # 检查内存（至少需要 512MB）
        try:
            import psutil
            mem = psutil.virtual_memory()
            available_mb = mem.available / (1024 ** 2)
            if available_mb < 512:
                return False, f"内存不足，仅剩余 {available_mb:.2f} MB，建议至少 512MB"
        except ImportError:
            pass  # psutil 不是必须的
        
        return True, "资源充足"

    def _find_local_blog_root(self) -> tuple[str, bool, bool]:
        """智能查找本地已部署的 Firefly 博客目录
        
        返回: (博客路径, 是否已构建, 是否自动检测)
        """
        configured = self.config.get("local_blog_root", "")
        
        # 如果配置了路径且存在，直接使用
        if configured and os.path.isdir(configured):
            is_built = self._is_blog_built(configured)
            return configured, is_built, False

        # 常见的博客目录搜索路径
        search_paths = [
            # 当前目录及子目录
            os.path.join(os.getcwd(), "firefly"),
            os.path.join(os.getcwd(), "blog"),
            os.getcwd(),
            # 常见 Linux 路径
            "/var/www/firefly",
            "/var/www/blog",
            "/var/www/html/firefly",
            "/usr/share/nginx/html/firefly",
            # 常见 Windows 路径
            "D:\\www\\firefly",
            "D:\\www\\blog",
            "C:\\www\\firefly",
            "C:\\www\\blog",
            # 用户目录
            os.path.join(os.path.expanduser("~"), "firefly"),
            os.path.join(os.path.expanduser("~"), "blog"),
            os.path.join(os.path.expanduser("~"), "projects", "firefly"),
        ]

        # 搜索子目录
        for base_path in ["/var/www", "/usr/share/nginx/html", "D:\\www", "C:\\www"]:
            if os.path.isdir(base_path):
                for name in os.listdir(base_path):
                    full_path = os.path.join(base_path, name)
                    if os.path.isdir(full_path):
                        search_paths.append(full_path)

        # 检查路径是否包含 Firefly 博客特征文件
        for path in search_paths:
            if self._is_firefly_blog(path):
                is_built = self._is_blog_built(path)
                logger.info(f"[Firefly] 自动检测到博客目录: {path} (已构建: {is_built})")
                return path, is_built, True

        # 未找到，返回默认值
        return DEFAULT_BLOG_ROOT, False, False

    def _init_components(self):
        """根据配置初始化文件系统、博客管理器和构建部署管理器"""
        deploy_mode = DeployMode(self.config.get("deploy_mode", "local_build"))
        
        # local_build 和 local_only 模式下智能检测博客目录
        blog_root = DEFAULT_BLOG_ROOT
        blog_found = False
        blog_built = False
        
        if deploy_mode in (DeployMode.LOCAL_BUILD, DeployMode.LOCAL_ONLY):
            blog_root, blog_built, auto_detected = self._find_local_blog_root()
            blog_found = auto_detected
            
            # 如果自动检测到博客目录，更新配置
            if auto_detected:
                self.config["local_blog_root"] = blog_root
                logger.info(f"[Firefly] 已更新配置 local_blog_root: {blog_root}")
        
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
        event,
        title: str,
        content: str,
        tags: str = "",
        category: str = "",
        description: str = "",
        draft: bool = False,
        slug: str = "",
        pinned: bool = False,
        image: str = "",
        author: str = "",
        comment: bool = True,
    ):
        '''在 Firefly 博客上创建一篇新文章。

        Args:
            title(string): 文章标题
            content(string): 文章正文内容（Markdown 格式）
            tags(string): 文章标签，多个标签用逗号分隔
            category(string): 文章分类
            description(string): 文章描述/摘要
            draft(boolean): 是否为草稿
            slug(string): 自定义文章URL路径（不设置则使用文件名）
            pinned(boolean): 是否置顶文章
            image(string): 文章封面图片路径
            author(string): 文章作者
            comment(boolean): 是否启用评论功能，默认启用
        '''
        if not self.blog_manager:
            yield "❌ 博客管理器未初始化"
            return

        filename = FilenameUtil.resolve(title)

        if await self.blog_manager.exists(filename):
            yield f"❌ 文章《{title}》已存在。"
            return

        tag_list = [t.strip() for t in tags.split(",") if t.strip()]
        extra: dict = {}
        if category:
            extra["category"] = category
        if description:
            extra["description"] = description
        if draft:
            extra["draft"] = True
        if slug:
            extra["slug"] = slug
        if pinned:
            extra["pinned"] = True
        if image:
            extra["image"] = image
        if author:
            extra["author"] = author
        if not comment:
            extra["comment"] = False

        full_content = self._build_post_content(title, content, tag_list, **extra)

        if await self.blog_manager.write_post(filename, full_content):
            yield f"✅ 文章《{title}》创建成功！\n提示：创建文章后需要重新构建部署才能生效。"
        else:
            yield f"❌ 创建文章《{title}》失败。"

    @filter.llm_tool(name="delete_blog_post")
    async def delete_post(self, event, title: str):
        '''删除 Firefly 博客上的一篇文章。

        Args:
            title(string): 文章标题或文件名
        '''
        if not self.blog_manager:
            yield "❌ 博客管理器未初始化"
            return

        filename = FilenameUtil.resolve(title)

        if not await self.blog_manager.exists(filename):
            yield f"❌ 文章《{title}》不存在。"
            return

        if await self.blog_manager.delete_post(filename):
            yield f"✅ 文章《{title}》已删除。\n提示：删除后需要重新构建部署才能生效。"
        else:
            yield f"❌ 删除文章《{title}》失败。"

    @filter.llm_tool(name="list_blog_posts")
    async def list_posts(self, event):
        '''列出 Firefly 博客上的所有文章。'''
        if not self.blog_manager:
            yield "❌ 博客管理器未初始化"
            return

        posts = await self.blog_manager.list_posts()
        yield self._format_post_list(posts)

    @filter.llm_tool(name="get_blog_post")
    async def get_post(self, event, title: str):
        '''获取 Firefly 博客上指定文章的完整内容。

        Args:
            title(string): 文章标题或文件名
        '''
        if not self.blog_manager:
            yield "❌ 博客管理器未初始化"
            return

        filename = FilenameUtil.resolve(title)

        if not await self.blog_manager.exists(filename):
            yield f"❌ 文章《{title}》不存在。"
            return

        content = await self.blog_manager.read_post(filename)
        if content is None:
            yield f"❌ 读取文章《{title}》失败。"
            return

        yield f"📄 文章《{title}》内容：\n\n{content}"

    @filter.llm_tool(name="update_blog_post")
    async def update_post(
        self,
        event,
        title: str,
        new_content: str = "",
        new_title: str = "",
        tags: str = "",
        category: str = "",
        description: str = "",
        slug: str = "",
        pinned: bool = None,
        image: str = "",
        author: str = "",
        comment: bool = None,
    ):
        '''更新 Firefly 博客上的一篇文章。

        Args:
            title(string): 要更新的文章标题或文件名
            new_content(string): 新的文章正文，为空则不修改
            new_title(string): 新的文章标题，为空则不修改
            tags(string): 新的标签，为空则不修改
            category(string): 新的分类，为空则不修改
            description(string): 新的描述，为空则不修改
            slug(string): 自定义文章URL路径，为空则不修改
            pinned(boolean): 是否置顶文章，为None则不修改
            image(string): 文章封面图片路径，为空则不修改
            author(string): 文章作者，为空则不修改
            comment(boolean): 是否启用评论功能，为None则不修改
        '''
        if not self.blog_manager:
            yield "❌ 博客管理器未初始化"
            return

        old_filename = FilenameUtil.resolve(title)

        if not await self.blog_manager.exists(old_filename):
            yield f"❌ 文章《{title}》不存在。"
            return

        old_content = await self.blog_manager.read_post(old_filename)
        if old_content is None:
            yield f"❌ 读取文章《{title}》失败。"
            return

        metadata, body = PostMetadata.from_content(old_content)

        # 处理标题变更（需要重命名文件）
        if new_title and new_title != metadata.title:
            new_filename = FilenameUtil.resolve(new_title)
            if await self.blog_manager.exists(new_filename):
                yield f"❌ 目标文章《{new_title}》已存在。"
                return

            metadata.title = new_title
            metadata.updated = datetime.now().strftime("%Y-%m-%d")
            
            # 同时应用其他更新
            if new_content:
                body = new_content
            if tags:
                metadata.tags = [t.strip() for t in tags.split(",") if t.strip()]
            if category:
                metadata.category = category
            if description:
                metadata.description = description
            if slug:
                metadata.slug = slug
            if pinned is not None:
                metadata.pinned = pinned
            if image:
                metadata.image = image
            if author:
                metadata.author = author
            if comment is not None:
                metadata.comment = comment

            new_full_content = metadata.to_yaml() + "\n" + body

            if await self.blog_manager.write_post(new_filename, new_full_content):
                await self.blog_manager.delete_post(old_filename)
                yield f"✅ 文章已重命名为《{new_title}》。\n提示：需要重新构建部署才能生效。"
            else:
                yield "❌ 更新文章失败。"
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
        if slug:
            metadata.slug = slug
        if pinned is not None:
            metadata.pinned = pinned
        if image:
            metadata.image = image
        if author:
            metadata.author = author
        if comment is not None:
            metadata.comment = comment

        metadata.updated = datetime.now().strftime("%Y-%m-%d")
        full_content = metadata.to_yaml() + "\n" + body

        if await self.blog_manager.write_post(old_filename, full_content):
            yield f"✅ 文章《{metadata.title}》更新成功！\n提示：需要重新构建部署才能生效。"
        else:
            yield f"❌ 更新文章《{metadata.title}》失败。"

    @filter.llm_tool(name="search_blog_posts")
    async def search_posts(self, event, keyword: str):
        '''在 Firefly 博客中搜索文章。

        Args:
            keyword(string): 搜索关键词
        '''
        if not self.blog_manager:
            yield "❌ 博客管理器未初始化"
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
            yield f"🔍 未找到包含「{keyword}」的文章。"
        else:
            yield self._format_post_list(results)

    # ========================================================================
    # 构建部署 LLM 工具
    # ========================================================================

    @filter.llm_tool(name="check_blog_environment")
    async def check_environment(self, event):
        '''检查 Firefly 博客的构建环境是否就绪（Node.js 和 pnpm）。'''
        if not self.build_manager:
            yield "❌ 构建管理器未初始化"
            return

        ok, msg = await self.build_manager.check_environment()
        if ok:
            yield f"✅ {msg}"
        else:
            yield f"❌ {msg}"

    @filter.llm_tool(name="install_blog_dependencies")
    async def install_dependencies(self, event):
        '''安装 Firefly 博客的依赖（执行 pnpm install）。'''
        if not self.build_manager:
            yield "❌ 构建管理器未初始化"
            return

        ok, msg = await self.build_manager.install_dependencies()
        if ok:
            yield f"✅ {msg}"
        else:
            yield f"❌ {msg}"

    @filter.llm_tool(name="build_blog")
    async def build_blog(self, event):
        '''构建 Firefly 博客（执行 pnpm build）。构建可能需要较长时间。'''
        if not self.build_manager:
            yield "❌ 构建管理器未初始化"
            return

        # 检查环境
        ok, msg = await self.build_manager.check_environment()
        if not ok:
            yield f"❌ 环境检查失败: {msg}\n请先安装 Node.js 和 pnpm。"
            return

        # 检查依赖
        if not await self.build_manager.check_dependencies_installed():
            yield "⚠️ 依赖未安装，请先执行 install_blog_dependencies。"
            return

        ok, msg = await self.build_manager.build()
        if ok:
            yield f"✅ {msg}\n构建产物位于 dist/ 目录。"
        else:
            yield f"❌ {msg}"

    @filter.llm_tool(name="deploy_blog")
    async def deploy_blog(self, event):
        '''部署 Firefly 博客到 Web 服务器。将构建产物部署到配置的 Web 根目录。'''
        if not self.build_manager:
            yield "❌ 构建管理器未初始化"
            return

        ok, msg = await self.build_manager.deploy()
        if ok:
            yield f"✅ {msg}"
        else:
            yield f"❌ {msg}"

    @filter.llm_tool(name="auto_setup_blog")
    async def auto_setup_blog(self, event):
        '''智能检测并自动设置 Firefly 博客。自动执行：
        1. 遍历系统查找已克隆的 Firefly 博客仓库
        2. 检查是否已构建
        3. 如果未找到仓库，自动克隆到默认目录
        4. 如果找到但未构建，检查资源后自动构建
        5. 更新配置文件'''
        deploy_mode = DeployMode(self.config.get("deploy_mode", "local_build"))
        
        if deploy_mode not in (DeployMode.LOCAL_BUILD, DeployMode.LOCAL_ONLY):
            yield "❌ 智能设置仅支持 local_build 和 local_only 模式"
            return

        yield "🔍 正在搜索系统中的 Firefly 博客仓库..."
        
        # 查找博客目录
        blog_root, is_built, auto_detected = self._find_local_blog_root()
        
        if auto_detected:
            if is_built:
                yield f"✅ 检测到已构建的博客目录: {blog_root}"
                yield "📝 更新配置中..."
                self.config["local_blog_root"] = blog_root
                yield f"✅ 配置已更新，博客目录: {blog_root}"
                return
            else:
                yield f"⚠️ 检测到博客目录但未构建: {blog_root}"
                
                # 检查系统资源
                yield "📊 检查系统资源..."
                resources_ok, msg = self._check_system_resources()
                if not resources_ok:
                    yield f"❌ {msg}"
                    yield "建议释放资源后重新执行"
                    return
                yield f"✅ {msg}"
                
                # 更新配置
                self.config["local_blog_root"] = blog_root
                
                # 重新初始化组件
                self._init_components()
                
                # 自动构建
                yield "🚀 开始自动构建..."
                if not self.build_manager:
                    yield "❌ 构建管理器初始化失败"
                    return
                
                # 安装依赖
                yield "📦 安装依赖..."
                ok, msg = await self.build_manager.install_dependencies()
                if not ok:
                    yield f"❌ 依赖安装失败: {msg}"
                    return
                yield f"✅ {msg}"
                
                # 构建
                yield "🔨 构建博客..."
                ok, msg = await self.build_manager.build()
                if ok:
                    yield f"✅ {msg}"
                    yield f"🎉 博客设置完成！目录: {blog_root}"
                else:
                    yield f"❌ 构建失败: {msg}"
        else:
            yield "❌ 未找到已克隆的 Firefly 博客仓库"
            
            # 检查系统资源
            yield "📊 检查系统资源..."
            resources_ok, msg = self._check_system_resources()
            if not resources_ok:
                yield f"❌ {msg}"
                yield "建议释放资源后重新执行"
                return
            yield f"✅ {msg}"
            
            # 自动克隆仓库
            yield f"📥 准备克隆 Firefly 博客到: {blog_root}"
            if not self.build_manager:
                # 需要先初始化构建管理器
                self.local_executor = LocalExecutor()
                self.build_manager = BuildDeployManager(self.config, self.local_executor)
            
            msg = await self.build_manager._clone_blog_repo()
            if not msg.startswith("✅"):
                yield msg
                return
            yield msg
            
            # 更新配置
            self.config["local_blog_root"] = blog_root
            
            # 重新初始化组件
            self._init_components()
            
            # 自动构建
            yield "🚀 开始自动构建..."
            
            # 安装依赖
            yield "📦 安装依赖..."
            ok, msg = await self.build_manager.install_dependencies()
            if not ok:
                yield f"❌ 依赖安装失败: {msg}"
                return
            yield f"✅ {msg}"
            
            # 构建
            yield "🔨 构建博客..."
            ok, msg = await self.build_manager.build()
            if ok:
                yield f"✅ {msg}"
                yield f"🎉 博客设置完成！目录: {blog_root}"
            else:
                yield f"❌ 构建失败: {msg}"

    @filter.llm_tool(name="build_and_deploy_blog")
    async def build_and_deploy(self, event):
        '''一键构建并部署 Firefly 博客。自动执行：检查环境 -> 安装依赖 -> 构建 -> 部署。'''
        if not self.build_manager:
            yield "❌ 构建管理器未初始化"
            return

        results = []

        # 检查环境
        ok, msg = await self.build_manager.check_environment()
        if not ok:
            yield f"❌ 环境检查失败: {msg}"
            return
        results.append(f"✅ 环境检查: {msg}")

        # 检查依赖，未安装则自动安装
        if not await self.build_manager.check_dependencies_installed():
            ok, msg = await self.build_manager.install_dependencies()
            if not ok:
                yield f"❌ 依赖安装失败\n{msg}"
                return
            results.append(f"✅ 依赖安装: {msg}")

        # 构建
        ok, msg = await self.build_manager.build()
        if not ok:
            yield f"❌ 构建失败: {msg}"
            return
        results.append(f"✅ 构建: {msg}")

        # 部署
        ok, msg = await self.build_manager.deploy()
        if not ok:
            yield f"❌ 部署失败: {msg}"
            return
        results.append(f"✅ 部署: {msg}")

        yield "\n".join(results)

    # ========================================================================
    # 生命周期管理
    # ========================================================================

    async def terminate(self):
        """插件卸载/停用时清理资源（关闭 SSH 连接等）"""
        if self.remote_executor:
            await self.remote_executor.close()
            self.remote_executor = None
        logger.info("[Firefly] 插件资源已清理")
