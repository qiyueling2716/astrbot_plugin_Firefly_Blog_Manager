"""模块说明见 main.py 顶部文档。"""

from __future__ import annotations

import os

from astrbot.api import logger

from .filesystem import FileSystem
from .models import PostInfo, PostMetadata



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
        return os.path.join(self.posts_dir, filename)

    async def list_posts(self) -> list[PostInfo]:
        """列出所有文章，返回文章信息列表"""
        pattern = os.path.join(self.posts_dir, "*.md")
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
                    pinned=metadata.pinned,
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

