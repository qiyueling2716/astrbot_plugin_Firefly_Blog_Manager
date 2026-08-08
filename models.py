"""模块说明见 main.py 顶部文档。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Optional
import yaml

from astrbot.api import logger



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
        }
        
        # 日期字段特殊处理：转换为 date 对象以确保 YAML 输出为日期类型
        if self.published:
            date_obj = self._parse_date(self.published)
            if date_obj:
                data["published"] = date_obj
            else:
                data["published"] = self.published
        if self.updated:
            date_obj = self._parse_date(self.updated)
            if date_obj:
                data["updated"] = date_obj
            else:
                data["updated"] = self.updated
            
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

        # 使用自定义日期表示器确保日期正确输出
        yaml_str = self._safe_dump_with_dates(data)
        return f"---\n{yaml_str}---\n"

    def _parse_date(self, date_str: str) -> Optional[date]:
        """解析日期字符串为 datetime.date 对象
        
        Args:
            date_str: 日期字符串
            
        Returns:
            datetime.date 对象，如果解析失败返回 None
        """
        
        # 尝试多种常见格式
        formats = [
            '%Y-%m-%d',      # 2024-01-01
            '%Y/%m/%d',      # 2024/01/01
            '%Y-%m-%d %H:%M:%S',  # 2024-01-01 12:00:00
            '%Y/%m/%d %H:%M:%S',  # 2024/01/01 12:00:00
            '%d-%m-%Y',      # 01-01-2024
            '%d/%m/%Y',      # 01/01/2024
        ]
        
        for fmt in formats:
            try:
                dt = datetime.strptime(date_str.strip(), fmt)
                return dt.date()
            except ValueError:
                continue
        
        return None

    def _safe_dump_with_dates(self, data: dict) -> str:
        """安全地序列化数据，确保日期字段正确输出为日期类型"""
        
        class DateDumper(yaml.Dumper):
            def represent_data(self, data):
                if isinstance(data, date):
                    return self.represent_scalar('tag:yaml.org,2002:timestamp', str(data))
                return super().represent_data(data)
        
        return yaml.dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False, Dumper=DateDumper)

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


@dataclass
class Submission:
    """投稿数据模型"""
    id: str
    title: str
    content: str
    author_name: str = ""
    author_email: str = ""
    tags: str = ""
    category: str = ""
    description: str = ""
    submit_time: str = ""
    user_id: str = ""
    status: str = "pending"  # pending / approved / rejected
    reject_reason: str = ""
    ai_review: Optional[dict] = None  # AI 初审结果

    def to_dict(self) -> dict:
        result = {
            "id": self.id,
            "title": self.title,
            "content": self.content,
            "author_name": self.author_name,
            "author_email": self.author_email,
            "tags": self.tags,
            "category": self.category,
            "description": self.description,
            "submit_time": self.submit_time,
            "user_id": self.user_id,
            "status": self.status,
            "reject_reason": self.reject_reason,
        }
        if self.ai_review is not None:
            result["ai_review"] = self.ai_review
        return result

    @classmethod
    def from_dict(cls, d: dict) -> "Submission":
        return cls(
            id=d.get("id", ""),
            title=d.get("title", ""),
            content=d.get("content", ""),
            author_name=d.get("author_name", ""),
            author_email=d.get("author_email", ""),
            tags=d.get("tags", ""),
            category=d.get("category", ""),
            description=d.get("description", ""),
            submit_time=d.get("submit_time", ""),
            user_id=d.get("user_id", ""),
            status=d.get("status", "pending"),
            reject_reason=d.get("reject_reason", ""),
            ai_review=d.get("ai_review"),
        )


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
# 投稿状态映射
# ============================================================================

SUBMISSION_STATUS_TEXT = {
    "pending": "待审核",
    "approved": "已批准",
    "rejected": "已拒绝",
}

SUBMISSION_STATUS_EMOJI = {
    "pending": "⏳",
    "approved": "✅",
    "rejected": "❌",
}

SUBMISSION_STATUS_BADGE = {
    "pending": "[PENDING]",
    "approved": "[APPROVED]",
    "rejected": "[REJECTED]",
}

