"""模块说明见 main.py 顶部文档。"""

from __future__ import annotations

import glob
import os
import posixpath
import re
import shutil
from abc import ABC, abstractmethod
from typing import Optional

from astrbot.api import logger

from .executors import RemoteExecutor



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
            # 安全最佳实践：显式设置文件权限，避免依赖 umask
            os.chmod(path, 0o644)
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
                if isinstance(content, bytes):
                    content = content.decode("utf-8", errors="replace")
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
            
            async with sftp.open(path, "wb") as f:
                if isinstance(content, str):
                    content = content.encode("utf-8")
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
        # 安全检查：防止路径遍历
        if not title_or_filename:
            return "untitled.md"
        
        # 检测危险字符
        if ".." in title_or_filename:
            # 移除所有 ..
            cleaned = title_or_filename.replace("..", "")
            title_or_filename = cleaned or "untitled.md"
        
        # 处理路径分隔符：只取文件名部分
        # Windows 和 Linux 都要处理
        title_or_filename = title_or_filename.replace("\\", "/")
        if "/" in title_or_filename:
            title_or_filename = title_or_filename.split("/")[-1]
        
        # 移除盘符
        if re.match(r'^[A-Za-z]:', title_or_filename):
            title_or_filename = title_or_filename.split(":", 1)[-1]
            if title_or_filename.startswith("/"):
                title_or_filename = title_or_filename[1:]
        
        if title_or_filename.endswith(".md"):
            return title_or_filename
        return f"{FilenameUtil.sanitize(title_or_filename)}.md"
