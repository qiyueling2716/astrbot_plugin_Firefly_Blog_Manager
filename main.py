"""
AstrBot Firefly 博客管理插件 v1.4.0

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

import functools
import os
import posixpath
import re
import shlex
import glob
import shutil
import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, date
from enum import Enum
from typing import Optional, Dict, List, Any, Tuple, Union
import json

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
# 进阶语法功能元数据定义
# ============================================================================

ADVANCED_SYNTAX_FEATURES = {
    "github_card": {
        "name": "GitHub 仓库卡片",
        "key": "advanced_syntax_github_card",
        "category": "链接增强",
        "pros": [
            "可在文章中嵌入 GitHub 仓库动态信息卡片，展示 Star 数、Fork 数、描述等",
            "自动从 GitHub API 获取最新数据，卡片内容始终为最新",
            "适合技术博客引用开源项目，视觉效果专业",
        ],
        "cons": [
            "依赖 GitHub API 可用性，离线环境无法渲染",
            "每次页面加载都会发起 API 请求，增加加载时间",
            "API 有频率限制，高流量博客可能触发限流",
        ],
        "syntax": '::github{repo="owner/repo"}',
        "example": '::github{repo="CuteLeaf/Firefly"}',
        "guide": "在 Markdown 中直接使用 `::github{repo=\"owner/repo\"}` 语法。"
                "页面加载时，信息会从 GitHub API 获取并渲染为动态卡片。"
                "适合在技术文章中引用开源项目。",
    },
    "admonitions": {
        "name": "提醒框 (Admonitions)",
        "key": "advanced_syntax_admonitions",
        "category": "内容组织",
        "pros": [
            "支持 NOTE/TIP/IMPORTANT/WARNING/CAUTION 等多种类型，语义明确",
            "可选 4 种主题：GitHub、Obsidian、VitePress、Docusaurus",
            "适合突出重要信息、注意事项、警告等，提升文章可读性",
        ],
        "cons": [
            "使用过多会导致文章视觉碎片化，降低阅读流畅性",
            "不同主题语法略有差异，切换主题后需检查兼容性",
            "Docusaurus 风格使用 `:::` 语法，与 GitHub 风格不兼容",
        ],
        "syntax": "> [!TYPE] 标题\n> 内容",
        "example": "> [!NOTE] 注意\n> 这是一个重要提示。\n\n> [!WARNING] 警告\n> 此操作不可逆！",
        "guide": "Firefly 支持 4 种提醒框主题（GitHub/Obsidian/VitePress/Docusaurus），"
                "默认使用 GitHub 风格。基本语法：`> [!TYPE] 标题` + `> 内容`。"
                "可用类型：NOTE、TIP、IMPORTANT、WARNING、CAUTION。"
                "Obsidian 风格额外支持 abstract、info、todo、success、question、failure、danger、bug、example、quote。",
    },
    "spoiler": {
        "name": "剧透文本",
        "key": "advanced_syntax_spoiler",
        "category": "内容组织",
        "pros": [
            "可隐藏敏感或剧透内容，用户点击才显示，增强互动性",
            "支持内嵌 Markdown 语法（加粗、斜体等），灵活性高",
            "适合隐藏答案、剧透、额外信息等",
        ],
        "cons": [
            "移动端触控体验不佳（点击区域小），需要较大的点击目标",
            "被隐藏内容可能被搜索引擎忽略，不利于 SEO",
            "滥用会导致用户阅读体验下降",
        ],
        "syntax": ":spoiler[被隐藏的内容]",
        "example": "答案：:spoiler[42]",
        "guide": "使用 `:spoiler[内容]` 语法隐藏文本。"
                "隐藏内容中支持 Markdown 格式，如 `:spoiler[**加粗**的秘密]`。"
                "用户点击/触摸隐藏区域即可显示内容。",
    },
    "image_grid": {
        "name": "图片画廊网格",
        "key": "advanced_syntax_image_grid",
        "category": "多媒体",
        "pros": [
            "可将 2-4 张图片并排展示，自动裁剪对齐，视觉整齐",
            "响应式布局，自动适配不同屏幕尺寸",
            "图注恒定底端对齐，适合照片对比、画廊展示",
        ],
        "cons": [
            "比例不一致的图片会被裁剪（object-cover），完整内容需点击灯箱查看",
            "仅支持 2-4 张图片，无法展示更多",
            "被裁剪后部分图片内容不可见，建议使用相同比例的图片",
        ],
        "syntax": "[grid]\n![图片1](./img1.jpg)\n![图片2](./img2.jpg)\n[/grid]",
        "example": "[grid]\n![示例一](./firefly1.avif)\n![示例二](./firefly2.avif)\n[/grid]",
        "guide": "使用 `[grid]` 和 `[/grid]` 标签包裹图片。"
                "支持 2-4 张图片并排，系统自动响应式布局。"
                "同一行图片若比例不一致，会自动裁剪居中。"
                "建议使用相同长宽比的图片。",
    },
    "code_blocks": {
        "name": "代码块进阶 (Expressive Code)",
        "key": "advanced_syntax_code_blocks",
        "category": "代码展示",
        "pros": [
            "支持编辑器/终端框架，可设置文件名或终端标题",
            "支持行号、行高亮标记(diff/ins/del)、行标签",
            "支持可折叠区域、自动换行、ANSI 转义序列渲染",
            "适合技术教程和代码演示",
        ],
        "cons": [
            "语法复杂，配置项多，新手学习成本高",
            "部分功能依赖代码块元数据（如 title=\"xxx\"、showLineNumbers 等），需额外记忆",
            "Diff 语法与特定语言语法高亮混用时需注意兼容性",
        ],
        "syntax": "```lang title=\"文件名\" showLineNumbers\n// 代码\n```",
        "example": '```js title="app.js" showLineNumbers\n// 第1行\nconsole.log("Hello")\n// 第3行 - 标记\n```',
        "guide": "Firefly 使用 Expressive Code 渲染代码块。主要配置：\n"
                "- `title=\"文件名\"` — 显示编辑器框架和文件名\n"
                "- `title=\"Terminal window\"` — 终端框架样式\n"
                "- `frame=\"none\"` — 无框架\n"
                "- `showLineNumbers` — 显示行号\n"
                "- 行标记：在代码块元数据中标注行号，如 `\"第3行\"` 或 `\"7-8\"`\n"
                "- 标记类型：`mark`（默认蓝）、`ins`（绿色插入）、`del`（红色删除）\n"
                "- diff 语法：以 `+`/`-` 开头自动识别为 diff 标记\n"
                "- 折叠：在代码块中自动折叠样板代码",
    },
    "mermaid": {
        "name": "Mermaid 图表",
        "key": "advanced_syntax_mermaid",
        "category": "图表绘制",
        "pros": [
            "用纯文本描述即可生成多种图表，无需外部工具",
            "支持流程图、时序图、甘特图、类图、状态图、饼图",
            "与 Markdown 原生集成，版本管理友好",
        ],
        "cons": [
            "复杂图表语法冗长，维护成本高",
            "不支持所有图表类型（如 ER 图、部署图需用 PlantUML）",
            "渲染依赖客户端 JavaScript，某些阅读器不支持",
        ],
        "syntax": "```mermaid\n图表类型\n  语法描述\n```",
        "example": "```mermaid\nflowchart TD\n  A[开始] --> B{条件}\n  B -->|是| C[结果1]\n  B -->|否| D[结果2]\n```",
        "guide": "在代码块中指定 `mermaid` 语言即可。支持的图表类型：\n"
                "- `flowchart TD/LR` — 流程图\n"
                "- `sequenceDiagram` — 时序图\n"
                "- `gantt` — 甘特图\n"
                "- `classDiagram` — 类图\n"
                "- `stateDiagram` — 状态图\n"
                "- `pie` — 饼图\n\n"
                "Mermaid 适合轻量级图表，如需更丰富的图表类型（ER图、C4图等），"
                "请使用 PlantUML。",
    },
    "plantuml": {
        "name": "PlantUML 图表",
        "key": "advanced_syntax_plantuml",
        "category": "图表绘制",
        "pros": [
            "图表类型比 Mermaid 更丰富：活动图、用例图、组件图、部署图、ER图、C4架构图",
            "支持亮暗主题自动切换，缩放、拖拽和全屏交互",
            "语法高度结构化，适合软件工程文档",
        ],
        "cons": [
            "依赖外部 PlantUML 服务器渲染 SVG，内网环境可能需要自建服务",
            "渲染速度比 Mermaid 慢（需要网络请求）",
            "语法比 Mermaid 更复杂，学习曲线更陡",
        ],
        "syntax": "```plantuml\n@startuml\n  图表定义\n@enduml\n```",
        "example": "```plantuml\n@startuml\nactor User\nUser -> (登录)\nUser -> (查看文章)\n@enduml\n```",
        "guide": "在代码块中指定 `plantuml` 语言。Firefly 会在构建时编码并生成 SVG。\n"
                "支持的图表类型：\n"
                "- 活动图 (activity)\n"
                "- 时序图 (sequence)\n"
                "- 用例图 (usecase)\n"
                "- 类图 (class)\n"
                "- 组件图 (component)\n"
                "- 部署图 (deployment)\n"
                "- ER 图 (entity)\n"
                "- 状态图 (state)\n"
                "- C4 架构图\n\n"
                "PlantUML 适合需要丰富图表类型的软件工程文档。",
    },
    "katex": {
        "name": "KaTeX 数学公式",
        "key": "advanced_syntax_katex",
        "category": "学术/数学",
        "pros": [
            "渲染速度极快，不依赖外部服务",
            "支持行内公式、块级公式、矩阵、极限、求和、化学方程式",
            "适合数学、物理、计算机科学等技术博客",
        ],
        "cons": [
            "仅支持 LaTeX 数学模式子集，不支持 amsmath 的某些高级宏",
            "复杂公式占用较多垂直空间，可能影响阅读节奏",
            "不支持 \newcommand 等自定义命令",
        ],
        "syntax": "$行内公式$ 或 $$块级公式$$",
        "example": "欧拉公式：$e^{i\\pi} + 1 = 0$\n\n质能方程：$$E = mc^2$$",
        "guide": "行内公式使用单 `$` 包裹，块级公式使用双 `$$` 包裹。\n"
                "支持常见 LaTeX 数学语法：\n"
                "- 分数：`\\frac{分子}{分母}`\n"
                "- 矩阵：`\\begin{pmatrix} a & b \\\\ c & d \\end{pmatrix}`\n"
                "- 求和：`\\sum_{n=1}^{\\infty}`\n"
                "- 极限：`\\lim_{x \\to 0}`\n"
                "- 化学方程式：`\\ce{CH4 + 2O2 -> CO2 + 2H2O}`\n\n"
                "更多语法参考 KaTeX 官方文档。",
    },
}


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
# 命令执行器抽象
# ============================================================================

def _sanitize_command(command: str) -> str:
    """移除命令中的敏感信息（密码等），用于日志输出"""
    return re.sub(r"export SSHPASS='[^']*'", "export SSHPASS='***'", command)


def _validate_shell_command(command: str) -> bool:
    """检查命令是否包含危险的 shell 注入模式

    作为纵深防御手段，在命令执行前检测 $(...) 和反引号等命令替换语法。
    所有用户可控参数应已在调用前通过 shlex.quote() 转义，此检查仅作
    最后一道防线。
    """
    dangerous = [
        (r'\$\(', '$(...) 命令替换'),
        (r'`[^`]+`', '反引号命令替换'),
    ]
    for pattern, desc in dangerous:
        if re.search(pattern, command):
            logger.warning(f"[Security] 命令包含潜在危险的 shell 模式 ({desc})，已拒绝执行")
            logger.debug(f"[Security] 被拒绝的命令: {_sanitize_command(command)}")
            return False
    return True


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
        """执行命令，返回 (returncode, stdout, stderr)

        安全说明：使用 create_subprocess_shell 是为了支持包含 shell 特性
        （管道、重定向、&& 链式命令）的构建命令。所有用户可控的参数（路径、
        主机名等）在调用前已通过 shlex.quote() 进行转义。执行前会进行
        命令注入模式检测作为最后一道防线。
        """
        logger.debug(f"[LocalExecutor] 执行命令: {_sanitize_command(command)}")

        # 安全最佳实践：执行前检查命令是否包含危险的 shell 注入模式
        if not _validate_shell_command(command):
            return -1, "", "命令包含潜在危险的 shell 模式，已被拒绝执行"
        
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
                    logger.debug(f"[LocalExecutor] 命令执行失败: {_sanitize_command(command)}, 返回码: {returncode}, 错误: {stderr.decode('utf-8', errors='replace')}")
                
                return (
                    returncode,
                    stdout.decode("utf-8", errors="replace"),
                    stderr.decode("utf-8", errors="replace"),
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                logger.error(f"[LocalExecutor] 命令执行超时: {_sanitize_command(command)}")
                return -1, "", f"命令执行超时（{timeout}秒）"
        except PermissionError:
            logger.error(f"[LocalExecutor] 权限不足: {_sanitize_command(command)}")
            return -1, "", "权限不足，请检查文件或目录权限"
        except FileNotFoundError:
            logger.error(f"[LocalExecutor] 命令未找到: {_sanitize_command(command)}")
            return -1, "", "命令未找到，请检查是否安装了相关工具"
        except Exception as e:
            logger.error(f"[LocalExecutor] 执行命令异常: {_sanitize_command(command)}, 错误: {e}")
            return -1, "", "命令执行异常，请检查日志获取详细信息"

    async def close(self):
        """关闭执行器（本地执行器无需特殊清理）"""
        pass


class ConnectionStatus(Enum):
    """SSH 连接状态枚举"""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"

class RemoteExecutor(CommandExecutor):
    """远程 SSH 命令执行器，使用 asyncssh 异步连接，按需建立，带保活和重连"""

    def __init__(self, config: AstrBotConfig):
        self.config = config
        self._conn = None
        self._sftp = None
        self._lock = asyncio.Lock()
        self._connect_attempts = 0
        self._max_connect_attempts = 3
        self._status = ConnectionStatus.DISCONNECTED
        self._last_error = None

    @property
    def status(self) -> ConnectionStatus:
        """获取当前连接状态"""
        return self._status

    async def _ensure_connected(self, retry: int = 0):
        """确保 SSH 连接已建立（带锁防止并发连接竞争，支持重试）"""
        async with self._lock:
            # 如果正在连接，等待完成
            if self._status == ConnectionStatus.CONNECTING:
                await asyncio.sleep(0.5)
                return await self._ensure_connected(retry)

            # 如果已有连接，检查是否存活
            if self._conn is not None and self._status == ConnectionStatus.CONNECTED:
                try:
                    # 发送 keepalive 探测
                    await self._conn.run("echo ok", timeout=5)
                    return
                except Exception as e:
                    logger.warning(f"[SSH] 连接已断开，尝试重连: {e}")
                    self._conn = None
                    self._sftp = None
                    self._status = ConnectionStatus.DISCONNECTED

            try:
                import asyncssh
            except ImportError:
                self._status = ConnectionStatus.ERROR
                raise RuntimeError("远程模式需要 asyncssh 库，请安装: pip install asyncssh")

            hostname = self.config.get("server_ip", "")
            port = self.config.get("server_port", DEFAULT_SSH_PORT)
            username = self.config.get("username", "")
            auth_type = self.config.get("auth_type", "key")

            if not hostname:
                self._status = ConnectionStatus.ERROR
                raise ConfigurationError("缺少 server_ip 配置")
            if not username:
                self._status = ConnectionStatus.ERROR
                raise ConfigurationError("缺少 username 配置")

            connect_options: dict = {
                "host": hostname,
                "port": port,
                "username": username,
                "keepalive_interval": SSH_KEEPALIVE_INTERVAL,
                "keepalive_count_max": SSH_KEEPALIVE_COUNT_MAX,
                "connect_timeout": 15,
            }
            # 安全最佳实践：使用 known_hosts 进行主机密钥验证，防止中间人攻击
            # 如果配置了已知主机文件路径则使用，否则使用 asyncssh 默认的 ~/.ssh/known_hosts
            known_hosts_path = self.config.get("ssh_known_hosts_path", "").strip()
            if known_hosts_path:
                expanded = os.path.expanduser(known_hosts_path)
                connect_options["known_hosts"] = expanded
                logger.debug(f"[SSH] 使用 known_hosts: {expanded}")

            if auth_type == "password":
                password = self.config.get("password", "")
                if not password:
                    self._status = ConnectionStatus.ERROR
                    raise ConfigurationError("密码认证方式但未配置 password")
                connect_options["password"] = password
                logger.info("[SSH] 使用密码认证连接远程服务器")
            else:
                key_path = self.config.get("private_key_path", "")
                if key_path and os.path.exists(key_path):
                    connect_options["client_keys"] = [key_path]
                    logger.info("[SSH] 使用密钥认证连接远程服务器")
                else:
                    logger.warning(f"[SSH] 私钥文件不存在: {key_path}，尝试使用 SSH Agent")

            self._status = ConnectionStatus.CONNECTING
            try:
                self._conn = await asyncssh.connect(**connect_options)
                self._connect_attempts = 0
                self._status = ConnectionStatus.CONNECTED
                self._last_error = None
                logger.info("[SSH] 连接成功")
            except asyncssh.Error as e:
                self._connect_attempts += 1
                self._last_error = str(e)
                logger.error(f"[SSH] 连接失败 (第 {self._connect_attempts} 次): {e}")
                
                if self._connect_attempts < self._max_connect_attempts:
                    wait_time = 2 ** self._connect_attempts  # 指数退避
                    logger.info(f"[SSH] {wait_time} 秒后重试...")
                    await asyncio.sleep(wait_time)
                    await self._ensure_connected(retry + 1)
                else:
                    self._status = ConnectionStatus.ERROR
                    raise SSHConnectionError(f"SSH 连接失败，已重试 {self._max_connect_attempts} 次: {e}")

    async def run(self, command: str, cwd: Optional[str] = None, timeout: int = 300) -> tuple[int, str, str]:
        """执行远程命令，返回 (returncode, stdout, stderr)"""
        logger.debug(f"[RemoteExecutor] 执行命令: {_sanitize_command(command)}")
        
        try:
            await self._ensure_connected()

            if cwd:
                command = f"cd {cwd} && {command}"

            try:
                result = await self._conn.run(command, timeout=timeout)
                returncode = result.exit_status or 0
                
                if returncode != 0:
                    logger.debug(f"[RemoteExecutor] 命令执行失败: {_sanitize_command(command)}, 返回码: {returncode}")
                
                return returncode, result.stdout or "", result.stderr or ""
            except asyncssh.TimeoutError:
                logger.error(f"[RemoteExecutor] 命令执行超时: {_sanitize_command(command)}")
                # 超时不重置连接，可能是命令本身耗时太长
                return -1, "", f"命令执行超时（{timeout}秒）"
            except asyncssh.Error as e:
                logger.error(f"[RemoteExecutor] SSH 错误: {_sanitize_command(command)}, 错误: {e}")
                # 重置连接状态，下次自动重连
                self._conn = None
                self._sftp = None
                self._status = ConnectionStatus.DISCONNECTED
                self._last_error = str(e)
                return -1, "", "SSH 命令执行失败，请检查日志获取详细信息"
        except SSHConnectionError as e:
            return -1, "", "SSH 连接失败，请检查日志获取详细信息"
        except ConfigurationError as e:
            return -1, "", "SSH 配置错误，请检查日志获取详细信息"

    async def get_sftp(self):
        """获取 SFTP 客户端（复用已有连接）"""
        await self._ensure_connected()
        if self._sftp is None:
            try:
                self._sftp = await self._conn.start_sftp_client()
            except Exception as e:
                logger.error(f"[SSH] 创建 SFTP 客户端失败: {e}")
                raise
        return self._sftp

    async def close(self):
        """关闭 SSH 连接并清理资源"""
        async with self._lock:
            if self._sftp:
                try:
                    self._sftp.exit()
                except Exception as e:
                    logger.warning(f"[SSH] 关闭 SFTP 客户端失败: {e}")
                self._sftp = None
            
            if self._conn:
                try:
                    self._conn.close()
                except Exception as e:
                    logger.warning(f"[SSH] 关闭连接失败: {e}")
                self._conn = None
            
            self._status = ConnectionStatus.DISCONNECTED
            self._last_error = None
            logger.info("[SSH] 连接已关闭")

    def reset_connection(self):
        """主动重置连接状态（用于手动重连）"""
        self._conn = None
        self._sftp = None
        self._status = ConnectionStatus.DISCONNECTED
        self._connect_attempts = 0
        self._last_error = None
        logger.info("[SSH] 连接状态已重置")


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
        
        # 校验部署模式配置
        deploy_mode_value = config.get("deploy_mode", "local_build")
        if deploy_mode_value not in [m.value for m in DeployMode]:
            logger.warning(f"[BuildDeployManager] 无效的部署模式: {deploy_mode_value}，使用默认值 local_build")
            deploy_mode_value = "local_build"
        self.deploy_mode = DeployMode(deploy_mode_value)
        
        # 校验路径配置
        self.blog_root = self._validate_path(config.get("local_blog_root", "/var/www/firefly"), "local_blog_root")
        self.remote_blog_root = self._validate_path(config.get("remote_blog_root", "/var/www/firefly"), "remote_blog_root")
        self.web_root = self._validate_path(config.get("web_root", "/var/www/html"), "web_root")
        self.remote_web_root = self._validate_path(config.get("remote_web_root", "/var/www/html"), "remote_web_root")
        
        logger.info(f"[BuildDeployManager] 初始化完成 - 部署模式: {self.deploy_mode.value}")

    def _validate_path(self, path: str, config_name: str) -> str:
        """校验路径配置的有效性，防止路径遍历"""
        if not isinstance(path, str) or not path.strip():
            logger.warning(f"[BuildDeployManager] {config_name} 配置无效，使用默认路径")
            return "/var/www/firefly" if "blog" in config_name else "/var/www/html"
        # 安全最佳实践：规范化路径，防止路径遍历攻击
        return os.path.realpath(path.strip())

    async def _is_firefly_blog(self, path: str) -> bool:
        """检查路径是否为 Firefly 博客项目"""
        if self.deploy_mode == DeployMode.REMOTE_BUILD and self.remote_executor:
            package_json = posixpath.join(path, "package.json")
            src_content = posixpath.join(path, "src", "content", "posts")
            astro_config = posixpath.join(path, "astro.config.mjs")
            
            matches = 0
            rc, _, _ = await self.remote_executor.run(f"test -f {package_json}", timeout=5)
            if rc == 0:
                matches += 1
            rc, _, _ = await self.remote_executor.run(f"test -d {src_content}", timeout=5)
            if rc == 0:
                matches += 1
            rc, _, _ = await self.remote_executor.run(f"test -f {astro_config}", timeout=5)
            if rc == 0:
                matches += 1
            return matches >= 2
        else:
            if not os.path.isdir(path):
                return False
            package_json = os.path.join(path, "package.json")
            src_content = os.path.join(path, "src", "content", "posts")
            astro_config = os.path.join(path, "astro.config.mjs")
            matches = 0
            if os.path.isfile(package_json):
                matches += 1
            if os.path.isdir(src_content):
                matches += 1
            if os.path.isfile(astro_config):
                matches += 1
            return matches >= 2

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
            if not msg.startswith("[OK]"):
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
            if not msg.startswith("[OK]"):
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
            return "[ERROR] 未安装 git，请先安装 git\n\n解决方案:\n- Ubuntu/Debian: sudo apt install git\n- CentOS/RHEL: sudo yum install git\n- macOS: brew install git\n- Windows: 下载安装 git"
        
        # 创建父目录
        if self.deploy_mode == DeployMode.REMOTE_BUILD:
            parent_dir = posixpath.dirname(blog_root)
            await executor.run(f"mkdir -p {parent_dir}", timeout=10)
        else:
            parent_dir = os.path.dirname(blog_root)
            if parent_dir:
                os.makedirs(parent_dir, exist_ok=True)
        
        # 检查目标目录是否已存在且非空
        dir_exists = False
        if self.deploy_mode == DeployMode.REMOTE_BUILD:
            rc, out, err = await executor.run(f"ls -la {blog_root}", timeout=10)
            dir_exists = rc == 0
        else:
            dir_exists = os.path.isdir(blog_root)
        
        if dir_exists:
            # 检查目录是否为空
            is_empty = False
            if self.deploy_mode == DeployMode.REMOTE_BUILD:
                rc, out, err = await executor.run(f"ls -A {blog_root} | wc -l", timeout=10)
                is_empty = rc == 0 and (out.strip() == "0" or not out.strip())
            else:
                is_empty = len(os.listdir(blog_root)) == 0
            
            if not is_empty:
                # 目录已存在且非空，检查是否已经是 Firefly 博客
                if await self._is_firefly_blog(blog_root):
                    logger.info(f"[Build] 目标目录已存在且是 Firefly 博客，跳过克隆")
                    return f"[OK] 目标目录已存在且是 Firefly 博客: {blog_root}"
                else:
                    # 目录存在但不是 Firefly 博客，询问是否覆盖
                    return f"[ERROR] 目标目录已存在但不是 Firefly 博客\n目录: {blog_root}\n请手动清理该目录后重试，或在配置中指定其他路径"
        
        # 尝试克隆仓库
        rc, out, err = await executor.run(f"git clone {repo_url} {blog_root}", timeout=120)
        if rc != 0:
            logger.warning(f"[Build] 主仓库克隆失败，尝试镜像: {err}")
            rc, out, err = await executor.run(f"git clone {mirror_url} {blog_root}", timeout=120)
            if rc != 0:
                return f"[ERROR] 克隆博客仓库失败\n错误信息: {err}\n\n可能的解决方案:\n1. 检查网络连接\n2. 尝试手动克隆: git clone {repo_url} {blog_root}\n3. 检查目标目录是否有写入权限"
        
        return f"[OK] 成功克隆 Firefly 博客仓库到 {blog_root}"

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
            logger.error(f"[BuildDeployManager] 本地部署失败: {e}")
            return False, "部署失败，请检查日志获取详细信息"

    async def _run_sshpass(self, inner_cmd: str, timeout: int = 300) -> tuple[int, str, str]:
        """安全执行 sshpass 命令，密码通过临时文件传递而非环境变量

        避免密码出现在进程列表（/proc/*/environ）中，同时防止密码中的
        特殊字符（如单引号）破坏 shell 命令结构。
        """
        import tempfile
        password = self.config.get("password", "")
        fd, temp_path = tempfile.mkstemp()
        try:
            os.write(fd, password.encode())
            os.close(fd)
            os.chmod(temp_path, 0o600)
            cmd = f"sshpass -f {shlex.quote(temp_path)} {inner_cmd}"
            return await self.local_executor.run(cmd, timeout=timeout)
        finally:
            try:
                os.unlink(temp_path)
            except OSError:
                pass

    async def _deploy_local_to_remote(self):
        """本地构建后通过 rsync/scp 部署到远端"""
        if not self.remote_executor:
            return False, "本地构建模式需要配置远程 SSH"

        local_dist = os.path.join(self.blog_root, "dist")
        if not os.path.exists(local_dist):
            return False, f"本地构建产物不存在，请先构建\n预期路径: {local_dist}"

        hostname = self.config.get("server_ip", "")
        username = self.config.get("username", "")
        port = self.config.get("server_port", 22)
        auth_type = self.config.get("auth_type", "key")

        # 优先使用 rsync 部署
        # 安全最佳实践：使用 StrictHostKeyChecking=yes 防止中间人攻击
        # 关闭时使用 accept-new（首次自动接受，后续变更拒绝），比完全禁用安全
        strict_checking = self.config.get("ssh_strict_host_key_checking", True)
        checking_opt = "yes" if strict_checking else "accept-new"
        ssh_opts = f"-p {port} -o StrictHostKeyChecking={checking_opt}"
        if auth_type == "key":
            key_path = self.config.get("private_key_path", "")
            if key_path and os.path.exists(key_path):
                ssh_opts += f" -i {key_path}"
            rsync_cmd = (
                f'rsync -avz --delete '
                f'-e "ssh {ssh_opts}" '
                f'{shlex.quote(f"{local_dist}/")} {shlex.quote(f"{username}@{hostname}:{self.remote_web_root}/")}'
            )
        else:
            # 密码认证：使用 sshpass -f 从临时文件读取密码，避免密码出现在进程列表中
            password = self.config.get("password", "")
            if not password:
                return False, "密码认证模式下未配置密码"
            rsync_cmd = (
                f'rsync -avz --delete '
                f'-e "ssh {ssh_opts}" '
                f'{shlex.quote(f"{local_dist}/")} {shlex.quote(f"{username}@{hostname}:{self.remote_web_root}/")}'
            )

        if auth_type == "password":
            rc, out, err = await self._run_sshpass(rsync_cmd, timeout=300)
        else:
            rc, out, err = await self.local_executor.run(rsync_cmd, timeout=300)
        if rc != 0:
            # rsync 失败，检查是否是本地路径问题
            logger.warning(f"rsync 失败: {err}")
            
            # 检查本地 dist 目录内容
            if os.path.exists(local_dist):
                dist_contents = os.listdir(local_dist)
                if not dist_contents:
                    return False, f"本地构建产物目录为空: {local_dist}\n请重新执行构建"
            
            # 回退到 scp
            logger.info("尝试使用 scp 部署")
            return await self._deploy_via_scp(local_dist, hostname, username, port, auth_type)

        return True, f"已部署到 {hostname}:{self.remote_web_root}"

    async def _deploy_via_scp(self, local_dist: str, hostname: str, username: str, port: int, auth_type: str) -> tuple[bool, str]:
        """通过 scp 部署（rsync 失败时的回退方案）"""
        # 安全最佳实践：使用 StrictHostKeyChecking=yes 防止中间人攻击
        strict_checking = self.config.get("ssh_strict_host_key_checking", True)
        checking_opt = "yes" if strict_checking else "accept-new"
        ssh_opts = f"-P {port} -o StrictHostKeyChecking={checking_opt}"

        if auth_type == "key":
            key_path = self.config.get("private_key_path", "")
            if key_path and os.path.exists(key_path):
                ssh_opts += f" -i {key_path}"
            scp_cmd = (
                f'scp -r {ssh_opts} '
                f'{shlex.quote(f"{local_dist}/*")} {shlex.quote(f"{username}@{hostname}:{self.remote_web_root}/")}'
            )
        else:
            password = self.config.get("password", "")
            if not password:
                return False, "密码认证模式下未配置密码"
            await self.remote_executor.run(f"rm -rf {self.remote_web_root}/*")
            scp_cmd = (
                f'scp -r {ssh_opts} '
                f'{shlex.quote(f"{local_dist}/*")} {shlex.quote(f"{username}@{hostname}:{self.remote_web_root}/")}'
            )

        if auth_type == "password":
            rc, out, err = await self._run_sshpass(scp_cmd, timeout=300)
        else:
            rc, out, err = await self.local_executor.run(scp_cmd, timeout=300)
        if rc != 0:
            return False, f"scp 部署失败:\n{err}"
        return True, f"已通过 scp 部署到 {hostname}:{self.remote_web_root}"

    async def _deploy_remote(self):
        """远端构建后直接复制 dist/ 到远端 firefly 部署目录"""
        if not self.remote_executor:
            return False, "远程构建模式需要配置 SSH"

        # Firefly 博客的部署目标是 remote_blog_root 下的 dist/ 目录
        # 如果 remote_blog_root 是 /var/www/firefly，则部署到 /var/www/firefly
        deploy_target = self.remote_blog_root

        rc, out, err = await self.remote_executor.run(
            f"rm -rf {deploy_target}/* && cp -r {self.remote_blog_root}/dist/* {deploy_target}/",
            timeout=60,
        )
        if rc != 0:
            return False, f"远端部署失败:\n{err}"
        return True, f"已部署到远端 {deploy_target}"

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


# ============================================================================
# 装饰器定义
# ============================================================================

def require_admin(func):
    """管理员权限检查装饰器 - 使用 UMO 判定"""
    @functools.wraps(func)
    async def wrapper(self, event, *args, **kwargs):
        ok, msg = self._check_admin_permission(event)
        if not ok:
            yield msg
            return
        
        result = func(self, event, *args, **kwargs)
        # 兼容同步返回值、异步生成器和同步生成器
        if hasattr(result, '__aiter__'):
            async for item in result:
                yield item
        elif hasattr(result, '__iter__'):
            for item in result:
                yield item
        elif result is not None:
            yield result
    return wrapper

def require_blog_manager(func):
    """博客管理器检查装饰器"""
    @functools.wraps(func)
    async def wrapper(self, event, *args, **kwargs):
        if not self.blog_manager:
            yield "[ERROR] 博客管理器未初始化"
            return
        
        result = func(self, event, *args, **kwargs)
        # 兼容同步返回值、异步生成器和同步生成器
        if hasattr(result, '__aiter__'):
            async for item in result:
                yield item
        elif hasattr(result, '__iter__'):
            for item in result:
                yield item
        elif result is not None:
            yield result
    return wrapper

def require_build_manager(func):
    """构建管理器检查装饰器"""
    @functools.wraps(func)
    async def wrapper(self, event, *args, **kwargs):
        if not self.build_manager:
            yield "[ERROR] 构建管理器未初始化"
            return
        
        result = func(self, event, *args, **kwargs)
        # 兼容同步返回值、异步生成器和同步生成器
        if hasattr(result, '__aiter__'):
            async for item in result:
                yield item
        elif hasattr(result, '__iter__'):
            for item in result:
                yield item
        elif result is not None:
            yield result
    return wrapper

# ============================================================================
# 插件主类
# ============================================================================

@register(
    "astrbot_plugin_Firefly_Blog_Manager",
    "月凌",
    "通过 AI 指令管理 Firefly 博客文章和部署",
    "1.3.5",
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
        
        # 安全最佳实践：未配置 admin_umo 时发出警告
        if not self.config.get("admin_umo", "").strip():
            logger.warning("[Firefly] 未配置管理员 UMO，所有管理操作无需权限验证。请在生产环境中设置 admin_umo。")
        
        # 投稿持久化配置
        self._submissions_file = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), 
            "_submissions_cache.json"
        )
        self._submissions_cache = self._load_submissions()
    
    def _load_submissions(self) -> dict:
        """从文件加载投稿缓存"""
        try:
            if os.path.exists(self._submissions_file):
                with open(self._submissions_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            logger.warning(f"[Firefly] 加载投稿缓存失败: {e}")
        return {}
    
    def _save_submissions(self) -> bool:
        """保存投稿缓存到文件"""
        try:
            with open(self._submissions_file, 'w', encoding='utf-8') as f:
                json.dump(self._submissions_cache, f, ensure_ascii=False, indent=2)
            # 安全最佳实践：显式设置文件权限，避免依赖 umask
            os.chmod(self._submissions_file, 0o644)
            return True
        except Exception as e:
            logger.error(f"[Firefly] 保存投稿缓存失败: {e}")
            return False

    def _get_submission(self, submission_id: str) -> Optional[Submission]:
        """获取单个投稿对象"""
        d = self._submissions_cache.get(submission_id)
        return Submission.from_dict(d) if d else None

    def _list_submissions(self, status: str = "") -> List[Submission]:
        """列出投稿，可按状态过滤

        Args:
            status: 为空则返回全部，否则只返回 matching status
        """
        items = self._submissions_cache.values()
        if status:
            items = [s for s in items if s.get("status") == status]
        return sorted(
            [Submission.from_dict(s) for s in items],
            key=lambda x: x.submit_time,
            reverse=True,
        )

    def _get_submission_stats(self) -> dict:
        """获取投稿统计信息"""
        total = len(self._submissions_cache)
        pending = sum(1 for s in self._submissions_cache.values() if s.get("status") == "pending")
        approved = sum(1 for s in self._submissions_cache.values() if s.get("status") == "approved")
        rejected = sum(1 for s in self._submissions_cache.values() if s.get("status") == "rejected")
        return {"total": total, "pending": pending, "approved": approved, "rejected": rejected}

    def _store_ai_review_result(self, submission_id: str, review_result: dict):
        """将 AI 初审结果存入投稿记录"""
        if submission_id in self._submissions_cache:
            self._submissions_cache[submission_id]["ai_review"] = review_result
            self._save_submissions()

    async def _perform_ai_review(self, submission: dict) -> dict:
        """使用 AI 对投稿内容进行初审评估

        通过 AstrBot 的 tool_loop_agent 调用 LLM，从内容质量、格式规范、
        主题相关性、完整性等维度对投稿进行评估。

        Args:
            submission: 投稿字典

        Returns:
            {
                "passed": bool,           # 是否建议过审
                "score": int,             # 评分 1-10
                "summary": str,           # 一句话摘要
                "strengths": [str],       # 优点列表
                "issues": [str],          # 问题列表
                "suggestions": [str],     # 改进建议
                "review_time": str,       # 审核时间
            }
        """
        review_prompt = (
            f"你是一位严谨的博客内容审核编辑。请对以下投稿文章进行初审，从以下维度评估：\n\n"
            f"1. 内容质量：文章是否有实质内容？逻辑是否清晰？\n"
            f"2. 格式规范：Markdown 格式是否正确？标题层级是否合理？\n"
            f"3. 完整性：文章是否完整？是否有明显的未完待续？\n"
            f"4. 可读性：语言表达是否通顺？\n\n"
            f"=== 投稿信息 ===\n"
            f"标题：{submission.get('title', '无')}\n"
            f"作者：{submission.get('author_name', '匿名')}\n"
            f"分类：{submission.get('category', '未分类')}\n"
            f"标签：{submission.get('tags', '无')}\n"
            f"内容：\n{submission.get('content', '')[:3000]}\n"
            f"=== 投稿信息结束 ===\n\n"
            f"请以 JSON 格式返回评估结果，不要包含任何其他文字：\n"
            f'{{"passed": true/false, "score": 1-10, "summary": "一句话摘要", '
            f'"strengths": ["优点1", "优点2"], "issues": ["问题1", "问题2"], '
            f'"suggestions": ["建议1", "建议2"]}}\n\n'
            f"注意：passed 为 true 表示建议过审（质量合格），false 表示建议打回修改。"
        )

        try:
            # 使用 AstrBot 的 llm_generate 调用 LLM 进行纯文本分析
            # 无需 event 上下文，也不需要工具调用
            llm_resp = await self.context.llm_generate(
                chat_provider_id=None,  # 使用默认 provider
                prompt=review_prompt,
                system_prompt="你是一个严格的博客内容审核助手。请只返回 JSON 格式的评估结果，不要添加任何其他内容。",
            )

            # 从 LLM 响应中提取文本
            resp_text = ""
            if isinstance(llm_resp, str):
                resp_text = llm_resp
            elif hasattr(llm_resp, 'completion_text'):
                resp_text = llm_resp.completion_text
            elif hasattr(llm_resp, 'content'):
                resp_text = llm_resp.content
            elif isinstance(llm_resp, dict):
                resp_text = llm_resp.get("completion_text", str(llm_resp))

            # 尝试提取 JSON
            import json as json_module
            json_match = re.search(r'\{[\s\S]*\}', resp_text)
            if json_match:
                result = json_module.loads(json_match.group())
                result["review_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                # 确保必要字段
                result.setdefault("passed", False)
                result.setdefault("score", 5)
                result.setdefault("summary", "AI 初审完成")
                result.setdefault("strengths", [])
                result.setdefault("issues", [])
                result.setdefault("suggestions", [])
                return result
            else:
                logger.warning(f"[Firefly] AI 初审返回格式异常: {resp_text[:200]}")
                return {
                    "passed": None,
                    "score": 0,
                    "summary": "AI 初审未能完成（返回格式异常）",
                    "strengths": [],
                    "issues": ["AI 解析失败，请人工审核"],
                    "suggestions": [],
                    "review_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }

        except Exception as e:
            logger.error(f"[Firefly] AI 初审执行失败: {e}")
            return {
                "passed": None,
                "score": 0,
                "summary": "AI 初审服务暂时不可用，请稍后重试",
                "strengths": [],
                "issues": ["AI 初审未能完成评估，需人工审核"],
                "suggestions": ["请管理员进行人工审核"],
                "review_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }

    def _format_ai_review_result(self, review: dict) -> str:
        """格式化 AI 初审结果为可读文本"""
        if not review or review.get("score", 0) == 0:
            return ""

        passed = review.get("passed")
        score = review.get("score", 0)
        summary = review.get("summary", "")
        strengths = review.get("strengths", [])
        issues = review.get("issues", [])
        suggestions = review.get("suggestions", [])

        if passed is True:
            verdict = "**AI 初审建议：过审**"
        elif passed is False:
            verdict = "**AI 初审建议：打回修改**"
        else:
            verdict = "**AI 初审异常，请人工审核**"

        lines = [
            "",
            "---",
            "## AI 初审结果",
            "",
            verdict,
            f"**综合评分**: {score}/10",
            f"**摘要**: {summary}",
            "",
        ]

        if strengths:
            lines.append("### 优点")
            for s in strengths:
                lines.append(f"- {s}")
            lines.append("")

        if issues:
            lines.append("### 问题")
            for i in issues:
                lines.append(f"- {i}")
            lines.append("")

        if suggestions:
            lines.append("### 改进建议")
            for s in suggestions:
                lines.append(f"- {s}")
            lines.append("")

        if passed is True:
            lines.append("> 管理员可选择：**1. 过审**（批准发布）或 **2. 复审**（查看全文后决定）")
        elif passed is False:
            lines.append("> 管理员可选择：**1. 过审**（忽略建议直接批准）或 **2. 打回**（附修改意见拒绝）")

        return "\n".join(lines)

    def _get_umo(self, event) -> Optional[str]:
        """从事件对象中获取统一消息来源标识（UMO）
        
        UMO 格式通常为 platform:user_id，是 AstrBot 中唯一标识会话的标准方式。
        根据 skill-astrbot-dev 文档，event.unified_msg_origin 是标准字段。
        """
        # 优先从 event 获取 UMO（AstrBot 官方标准）
        umo = getattr(event, 'unified_msg_origin', None)
        if umo:
            return str(umo)
        
        # 尝试从 event.umo 获取（某些平台适配器可能使用此字段）
        umo = getattr(event, 'umo', None)
        if umo:
            return str(umo)
        
        # 尝试从其他常见字段构建 UMO
        # UMO 格式通常为: platform:user_id
        platform = getattr(event, 'platform_id', None) or getattr(event, 'platform', None)
        user_id = getattr(event, 'user_id', None)
        
        if platform and user_id:
            return f"{platform}:{user_id}"
        
        # 尝试从 sender 获取用户 ID 并构建 UMO
        sender = getattr(event, 'sender', None)
        if sender:
            user_id = getattr(sender, 'user_id', None) or getattr(sender, 'id', None)
            if platform and user_id:
                return f"{platform}:{user_id}"
        
        return None

    def _check_admin_permission(self, event) -> tuple[bool, str]:
        """检查用户是否有管理员权限（使用 UMO 判定）
        
        Args:
            event: 事件对象，包含 UMO 信息
        
        返回: (是否有权限, 错误消息或空字符串)
        
        支持两种配置格式：
        1. 完整 UMO 格式：platform:user_id（如 onebot:123456789）
        2. 仅用户 ID：纯数字（如 123456789），会自动匹配任意平台
        """
        admin_umo = self.config.get("admin_umo", "").strip()
        
        # 如果未配置管理员 UMO，允许所有操作（方便调试）
        if not admin_umo:
            return True, ""
        
        # 获取当前用户的 UMO
        current_umo = self._get_umo(event)
        
        if not current_umo:
            return False, "[ERROR] 无法获取用户标识（UMO），无法验证权限"
        
        # 比较 UMO
        # 支持两种匹配方式：
        # 1. 完整匹配：admin_umo 是完整格式（包含冒号）
        # 2. 仅用户 ID 匹配：admin_umo 是纯数字，只匹配 user_id 部分
        if ':' in admin_umo:
            # 完整格式，需要完全匹配
            if current_umo == admin_umo:
                return True, ""
        else:
            # 仅用户 ID 格式，匹配任意平台的该用户
            # current_umo 格式为 platform:user_id，提取 user_id 部分
            if ':' in current_umo:
                current_user_id = current_umo.split(':')[1]
                if current_user_id == admin_umo:
                    return True, ""
            else:
                # 如果 current_umo 也没有冒号，直接比较
                if current_umo == admin_umo:
                    return True, ""
        
        return False, "[ERROR] 权限不足：此操作仅允许管理员使用"

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
        try:
            # 跨平台磁盘路径选择
            disk_path = self._get_disk_path_for_check()
            disk_usage = shutil.disk_usage(disk_path)
            free_space_gb = disk_usage.free / (1024 ** 3)
            if free_space_gb < 0.5:
                return False, f"磁盘空间不足，仅剩余 {free_space_gb:.2f} GB，建议至少 500MB"
            logger.info(f"[Firefly] 磁盘空间检查通过: {free_space_gb:.2f} GB 可用")
        except PermissionError:
            logger.warning(f"[Firefly] 磁盘空间检查权限不足")
        except FileNotFoundError:
            logger.warning(f"[Firefly] 磁盘路径不存在")
        except Exception as e:
            logger.warning(f"[Firefly] 磁盘空间检查失败: {e}")
        
        # 检查内存（使用配置的阈值，默认 1536MB = 1.5GB）
        memory_threshold = self.config.get("build_memory_threshold", 1536)
        try:
            import psutil
            mem = psutil.virtual_memory()
            available_mb = mem.available / (1024 ** 2)
            total_mb = mem.total / (1024 ** 2)
            used_percent = mem.percent
            
            if available_mb < memory_threshold:
                return False, f"内存不足，仅剩余 {available_mb:.2f} MB（总内存 {total_mb:.0f} MB，使用率 {used_percent:.1f}%）。构建 Firefly 博客需要约 1.5GB 内存，建议设置 build_memory_threshold 为更低的值，或使用 remote_build 模式让远端服务器承担构建工作。"
            
            logger.info(f"[Firefly] 内存检查通过: {available_mb:.2f} MB 可用")
            return True, f"资源充足。可用内存: {available_mb:.2f} MB（总内存 {total_mb:.0f} MB，使用率 {used_percent:.1f}%）"
        except ImportError:
            logger.warning("[Firefly] psutil 未安装，跳过内存检查")
            return True, "资源检查：psutil 未安装，跳过内存检查"
        except Exception as e:
            logger.error(f"[Firefly] 内存检查失败: {e}")
            return True, f"资源检查：内存检查失败 ({e})，继续执行"

    def _get_disk_path_for_check(self) -> str:
        """获取用于磁盘空间检查的路径（跨平台兼容）"""
        # 优先使用博客目录所在磁盘
        blog_root = self.config.get("local_blog_root", "")
        if blog_root and os.path.isdir(blog_root):
            drive, _ = os.path.splitdrive(blog_root)
            if drive:
                return drive + os.sep if os.name == 'nt' else blog_root
        
        # 回退到系统默认路径
        if os.name == 'nt':
            # Windows: 尝试获取系统盘
            return os.path.join(os.environ.get('SYSTEMDRIVE', 'C:'), os.sep)
        else:
            # Unix-like: 使用根目录
            return "/"

    
    def _check_memory_status(self) -> tuple[bool, str]:
        """检查当前内存状态，返回详细信息"""
        try:
            import psutil
            mem = psutil.virtual_memory()
            available_mb = mem.available / (1024 ** 2)
            total_mb = mem.total / (1024 ** 2)
            used_percent = mem.percent
            memory_threshold = self.config.get("build_memory_threshold", 1536)
            
            status = "[OK]" if available_mb >= memory_threshold else "[WARNING]"
            return True, f"{status} 当前内存状态:\n- 总内存: {total_mb:.0f} MB\n- 可用内存: {available_mb:.2f} MB\n- 使用率: {used_percent:.1f}%\n- 构建阈值: {memory_threshold} MB\n- 是否满足构建条件: {'是' if available_mb >= memory_threshold else '否'}"
        except ImportError:
            return False, "[ERROR] psutil 未安装，无法检查内存状态。请安装 psutil: pip install psutil"

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
            # 常见的 root 用户目录（大小写变体）
            "/root/Firefly",
            "/root/firefly",
            "/root/blog",
        ]

        # 搜索子目录（包含大小写变体）
        for base_path in ["/var/www", "/usr/share/nginx/html", "/root", "D:\\www", "C:\\www"]:
            if os.path.isdir(base_path):
                for name in os.listdir(base_path):
                    full_path = os.path.join(base_path, name)
                    if os.path.isdir(full_path):
                        # 检查目录名是否包含 firefly 或 blog（大小写不敏感）
                        name_lower = name.lower()
                        if "firefly" in name_lower or "blog" in name_lower:
                            search_paths.append(full_path)

        # 检查路径是否包含 Firefly 博客特征文件（大小写不敏感）
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
        if deploy_mode in (DeployMode.LOCAL_BUILD, DeployMode.LOCAL_ONLY):
            blog_root, _, auto_detected = self._find_local_blog_root()
            
            # 如果自动检测到博客目录，更新配置
            if auto_detected:
                self.config["local_blog_root"] = blog_root
                logger.info(f"[Firefly] 已更新配置 local_blog_root: {blog_root}")
        else:
            # 远程构建模式使用配置的远程路径
            blog_root = self.config.get("remote_blog_root", DEFAULT_BLOG_ROOT)
        
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
            return "[INFO] 博客目前没有文章"

        lines = [f"[INFO] 当前共有 {len(posts)} 篇文章:"]
        for post in posts:
            status = "[PUBLISHED]" if not post.draft else "[DRAFT]"
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
    @require_admin
    @require_blog_manager
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
        filename = FilenameUtil.resolve(title)
        logger.info(f"[Firefly] 尝试创建文章: {title} (文件: {filename})")

        if await self.blog_manager.exists(filename):
            logger.warning(f"[Firefly] 文章已存在: {title}")
            yield f"[ERROR] 文章《{title}》已存在"
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
            logger.info(f"[Firefly] 文章创建成功: {title}")
            yield f"[OK] 文章《{title}》创建成功\n提示：创建文章后需要重新构建部署才能生效"
        else:
            logger.error(f"[Firefly] 文章创建失败: {title}")
            yield f"[ERROR] 创建文章《{title}》失败"

    @filter.llm_tool(name="delete_blog_post")
    @require_admin
    @require_blog_manager
    async def delete_post(self, event, title: str):
        '''删除 Firefly 博客上的一篇文章。

        Args:
            title(string): 文章标题或文件名
        '''
        filename = FilenameUtil.resolve(title)

        if not await self.blog_manager.exists(filename):
            yield f"[ERROR] 文章《{title}》不存在"
            return

        if await self.blog_manager.delete_post(filename):
            yield f"[OK] 文章《{title}》已删除\n提示：删除后需要重新构建部署才能生效"
        else:
            yield f"[ERROR] 删除文章《{title}》失败"

    @filter.llm_tool(name="list_blog_posts")
    @require_blog_manager
    async def list_posts(self, event):
        '''列出 Firefly 博客上的所有文章。'''
        posts = await self.blog_manager.list_posts()
        yield self._format_post_list(posts)

    @filter.llm_tool(name="get_blog_post")
    @require_blog_manager
    async def get_post(self, event, title: str):
        '''获取 Firefly 博客上指定文章的完整内容。

        Args:
            title(string): 文章标题或文件名
        '''

        filename = FilenameUtil.resolve(title)

        if not await self.blog_manager.exists(filename):
            yield f"[ERROR] 文章《{title}》不存在"
            return

        content = await self.blog_manager.read_post(filename)
        if content is None:
            yield f"[ERROR] 读取文章《{title}》失败"
            return

        yield f"[INFO] 文章《{title}》内容:\n\n{content}"

    @filter.llm_tool(name="update_blog_post")
    @require_admin
    @require_blog_manager
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
        old_filename = FilenameUtil.resolve(title)

        if not await self.blog_manager.exists(old_filename):
            yield f"[ERROR] 文章《{title}》不存在"
            return

        old_content = await self.blog_manager.read_post(old_filename)
        if old_content is None:
            yield f"[ERROR] 读取文章《{title}》失败"
            return

        metadata, body = PostMetadata.from_content(old_content)

        # 处理标题变更（需要重命名文件）
        if new_title and new_title != metadata.title:
            new_filename = FilenameUtil.resolve(new_title)
            if await self.blog_manager.exists(new_filename):
                yield f"[ERROR] 目标文章《{new_title}》已存在"
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
                yield f"[OK] 文章已重命名为《{new_title}》\n提示：需要重新构建部署才能生效"
            else:
                yield "[ERROR] 更新文章失败"
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
            yield f"[OK] 文章《{metadata.title}》更新成功\n提示：需要重新构建部署才能生效"
        else:
            yield f"[ERROR] 更新文章《{metadata.title}》失败"

    @filter.llm_tool(name="search_blog_posts")
    @require_blog_manager
    async def search_posts(self, event, keyword: str):
        '''在 Firefly 博客中搜索文章。

        Args:
            keyword(string): 搜索关键词
        '''

        posts = await self.blog_manager.list_posts()
        results = []
        keyword_lower = keyword.lower()

        for post in posts:
            if (keyword_lower in post.title.lower()
                    or keyword_lower in post.category.lower()
                    or any(keyword_lower in t.lower() for t in post.tags)):
                results.append(post)

        if not results:
            yield f"[INFO] 未找到包含「{keyword}」的文章"
        else:
            yield self._format_post_list(results)

    # ========================================================================
    # 构建部署 LLM 工具
    # ========================================================================

    @filter.llm_tool(name="check_blog_environment")
    @require_build_manager
    async def check_environment(self, event):
        '''检查 Firefly 博客的构建环境是否就绪（Node.js 和 pnpm）。'''
        ok, msg = await self.build_manager.check_environment()
        prefix = "[OK]" if ok else "[ERROR]"
        yield f"{prefix} {msg}"

    @filter.llm_tool(name="install_blog_dependencies")
    @require_admin
    @require_build_manager
    async def install_dependencies(self, event):
        '''安装 Firefly 博客的依赖（执行 pnpm install）。需要主人权限。'''

        ok, msg = await self.build_manager.install_dependencies()
        prefix = "[OK]" if ok else "[ERROR]"
        yield f"{prefix} {msg}"

    @filter.llm_tool(name="build_blog")
    @require_admin
    @require_build_manager
    async def build_blog(self, event):
        '''构建 Firefly 博客（执行 pnpm build）。构建可能需要较长时间，占用约 1.5GB 内存。需要主人权限。'''
        # 检查环境
        ok, msg = await self.build_manager.check_environment()
        if not ok:
            yield f"[ERROR] 环境检查失败: {msg}\n请先安装 Node.js 和 pnpm"
            return

        # 检查依赖
        if not await self.build_manager.check_dependencies_installed():
            yield "[WARNING] 依赖未安装，请先执行 install_blog_dependencies"
            return

        # 检查内存是否满足构建条件
        ok, msg = self._check_system_resources()
        if not ok:
            yield f"[ERROR] {msg}"
            return

        yield f"[INFO] {msg}"

        ok, msg = await self.build_manager.build()
        if ok:
            yield f"[OK] {msg}\n构建产物位于 dist/ 目录"
        else:
            yield f"[ERROR] {msg}"

    @filter.llm_tool(name="check_memory_status")
    async def check_memory_status(self, event):
        '''检查当前系统内存状态，判断是否满足构建条件。
        
        返回当前总内存、可用内存、使用率以及是否满足构建阈值。
        '''
        ok, msg = self._check_memory_status()
        yield msg

    @filter.llm_tool(name="check_build_resource")
    async def check_build_resource(self, event):
        '''检查构建博客所需的资源是否充足（磁盘空间和内存）。
        
        构建 Firefly 博客需要约 1.5GB 内存和 500MB 磁盘空间。
        '''
        ok, msg = self._check_system_resources()
        prefix = "[OK]" if ok else "[ERROR]"
        yield f"{prefix} {msg}"

    @filter.llm_tool(name="get_build_config")
    async def get_build_config(self, event):
        '''获取当前构建相关的配置信息，包括内存阈值、内存限制和并发设置。'''
        memory_threshold = self.config.get("build_memory_threshold", 1536)
        memory_limit = self.config.get("build_memory_limit", 0)
        allow_concurrent = self.config.get("allow_build_concurrent", False)
        
        config_info = f"[INFO] 当前构建配置:\n"
        config_info += f"- build_memory_threshold: {memory_threshold} MB（可用内存低于此值时跳过构建）\n"
        config_info += f"- build_memory_limit: {'不限制' if memory_limit == 0 else f'{memory_limit} MB'}\n"
        config_info += f"- allow_build_concurrent: {'允许并发构建' if allow_concurrent else '不允许并发构建'}\n"
        config_info += f"\n[INFO] 提示：构建 Firefly 博客约需 1.5GB 内存，建议将 build_memory_threshold 设置为 1536 或更高"
        
        yield config_info

    @filter.llm_tool(name="deploy_blog")
    @require_admin
    @require_build_manager
    async def deploy_blog(self, event):
        '''部署 Firefly 博客到 Web 服务器。将构建产物部署到配置的 Web 根目录。需要主人权限。'''
        # 检查 dist 目录是否存在
        blog_root = self.build_manager.blog_root if self.build_manager else self.config.get("local_blog_root", DEFAULT_BLOG_ROOT)
        dist_path = os.path.join(blog_root, "dist")
        if self.build_manager and self.build_manager.deploy_mode == DeployMode.REMOTE_BUILD:
            # 远程模式下，检查远程 dist
            rc, _, _ = await self.build_manager.remote_executor.run(f"test -d {blog_root}/dist", timeout=5)
            if rc != 0:
                yield "[ERROR] 构建产物不存在，请先执行 build_blog"
                return
        elif not os.path.exists(dist_path):
            yield "[ERROR] 构建产物不存在，请先执行 build_blog"
            return

        ok, msg = await self.build_manager.deploy()
        prefix = "[OK]" if ok else "[ERROR]"
        yield f"{prefix} {msg}"

    @filter.llm_tool(name="auto_setup_blog")
    @require_admin
    async def auto_setup_blog(self, event):
        '''智能检测并自动设置 Firefly 博客。自动执行：
        1. 遍历系统查找已克隆的 Firefly 博客仓库
        2. 检查是否已构建
        3. 如果未找到仓库，自动克隆到默认目录
        4. 如果找到但未构建，检查资源后自动构建
        5. 更新配置文件。需要主人权限。'''
        async for result in self._do_auto_setup():
            yield result

    async def _do_auto_setup(self):
        """执行自动设置的内部逻辑（提取出来避免代码重复）"""
        deploy_mode = DeployMode(self.config.get("deploy_mode", "local_build"))
        
        if deploy_mode not in (DeployMode.LOCAL_BUILD, DeployMode.LOCAL_ONLY):
            yield "[ERROR] 智能设置仅支持 local_build 和 local_only 模式"
            return

        yield "[INFO] 正在搜索系统中的 Firefly 博客仓库..."
        
        # 查找博客目录
        blog_root, is_built, auto_detected = self._find_local_blog_root()
        
        if auto_detected and is_built:
            yield f"[OK] 检测到已构建的博客目录: {blog_root}"
            yield "[INFO] 更新配置中..."
            self.config["local_blog_root"] = blog_root
            yield f"[OK] 配置已更新，博客目录: {blog_root}"
            return
        
        # 以下两种情况需要构建：
        # 1. 找到目录但未构建
        # 2. 未找到目录，需要克隆
        need_clone = not auto_detected
        
        if auto_detected and not is_built:
            yield f"[WARNING] 检测到博客目录但未构建: {blog_root}"
        elif need_clone:
            yield "[ERROR] 未找到已克隆的 Firefly 博客仓库"
        
        # 检查系统资源
        yield "[INFO] 检查系统资源..."
        resources_ok, msg = self._check_system_resources()
        if not resources_ok:
            yield f"[ERROR] {msg}"
            yield "[INFO] 建议释放资源后重新执行"
            return
        yield f"[OK] {msg}"
        
        # 更新配置
        self.config["local_blog_root"] = blog_root
        
        # 确保构建管理器已初始化
        if not self.build_manager:
            self.local_executor = LocalExecutor()
            self.build_manager = BuildDeployManager(self.config, self.local_executor)
        
        # 重新初始化组件
        self._init_components()
        
        # 如果需要克隆仓库
        if need_clone:
            yield f"[INFO] 准备克隆 Firefly 博客到: {blog_root}"
            clone_msg = await self.build_manager._clone_blog_repo()
            yield clone_msg
            if not clone_msg.startswith("[OK]"):
                return
            self._init_components()
        
        # 执行安装依赖和构建
        yield "[INFO] 开始自动构建..."
        if not self.build_manager:
            yield "[ERROR] 构建管理器初始化失败"
            return
        
        yield "[INFO] 安装依赖..."
        ok, msg = await self.build_manager.install_dependencies()
        if not ok:
            yield f"[ERROR] 依赖安装失败: {msg}"
            return
        yield f"[OK] {msg}"
        
        yield "[INFO] 构建博客..."
        ok, msg = await self.build_manager.build()
        if ok:
            yield f"[OK] {msg}"
            yield f"[OK] 博客设置完成！目录: {blog_root}"
        else:
            yield f"[ERROR] 构建失败: {msg}"

    @filter.llm_tool(name="build_and_deploy_blog")
    @require_admin
    @require_build_manager
    async def build_and_deploy(self, event):
        '''一键构建并部署 Firefly 博客。自动执行：检查环境 -> 安装依赖 -> 构建 -> 部署。需要主人权限。'''
        results = []

        # 检查资源
        resources_ok, resources_msg = self._check_system_resources()
        if not resources_ok:
            yield f"[ERROR] 资源检查失败: {resources_msg}"
            return
        results.append(f"[OK] 资源检查: {resources_msg}")

        # 检查环境
        ok, msg = await self.build_manager.check_environment()
        if not ok:
            yield f"[ERROR] 环境检查失败: {msg}"
            return
        results.append(f"[OK] 环境检查: {msg}")

        # 检查依赖，未安装则自动安装
        if not await self.build_manager.check_dependencies_installed():
            ok, msg = await self.build_manager.install_dependencies()
            if not ok:
                yield f"[ERROR] 依赖安装失败\n{msg}"
                return
            results.append(f"[OK] 依赖安装: {msg}")

        # 构建
        ok, msg = await self.build_manager.build()
        if not ok:
            yield f"[ERROR] 构建失败: {msg}"
            return
        results.append(f"[OK] 构建: {msg}")

        # 部署
        ok, msg = await self.build_manager.deploy()
        if not ok:
            yield f"[ERROR] 部署失败: {msg}"
            return
        results.append(f"[OK] 部署: {msg}")

        yield "\n".join(results)

    # ========================================================================
    # 投稿管理 LLM 工具
    # ========================================================================

    @filter.llm_tool(name="submit_post_draft")
    async def submit_post_draft(
        self,
        event,
        title: str,
        content: str,
        author_name: str = "",
        author_email: str = "",
        tags: str = "",
        category: str = "",
        description: str = "",
    ):
        '''提交一篇文章草稿到博客。投稿不会立即发布，需要主人审核后才能发布。
        
        任何人都可以使用此功能提交投稿，无需权限验证。
        
        Args:
            title(string): 文章标题
            content(string): 文章正文内容（Markdown 格式）
            author_name(string): 作者姓名
            author_email(string): 作者邮箱
            tags(string): 文章标签，多个标签用逗号分隔
            category(string): 文章分类
            description(string): 文章描述/摘要
        '''
        import uuid
        
        submission_id = uuid.uuid4().hex[:12]
        submit_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        user_id = (
            getattr(event, 'user_id', None) or
            getattr(event, 'sender_id', None) or
            getattr(event, 'from_id', None) or
            getattr(event, 'user_id_holder', None)
        )
        if user_id is not None:
            user_id = str(user_id)
        
        submission = {
            "id": submission_id,
            "title": title,
            "content": content,
            "author_name": author_name,
            "author_email": author_email,
            "tags": tags,
            "category": category,
            "description": description,
            "submit_time": submit_time,
            "user_id": user_id,
            "status": "pending",
        }
        
        self._submissions_cache[submission_id] = submission
        
        # 持久化保存
        save_success = self._save_submissions()
        enable_ai_review = self.config.get("enable_ai_review", True)

        if save_success:
            if enable_ai_review:
                # 启用 AI 初审：先通知投稿成功 + AI 审核中
                yield (
                    f"[OK] 投稿成功\n\n"
                    f"投稿 ID: {submission_id}\n"
                    f"标题: {title}\n"
                    f"作者: {author_name or '匿名'}\n"
                    f"提交时间: {submit_time}\n\n"
                    f"AI 初审中，请稍候……"
                )

                # 执行 AI 初审
                review_result = await self._perform_ai_review(submission)
                self._store_ai_review_result(submission_id, review_result)

                # 展示初审结果
                review_text = self._format_ai_review_result(review_result)
                if review_text:
                    yield review_text
            else:
                # 未启用 AI 初审：仅发送提醒通知
                yield (
                    f"[OK] 投稿成功\n\n"
                    f"投稿 ID: {submission_id}\n"
                    f"标题: {title}\n"
                    f"作者: {author_name or '匿名'}\n"
                    f"提交时间: {submit_time}\n\n"
                    f"您的投稿已保存，等待主人审核。主人审核通过后，文章将正式发布到博客。\n"
                    f"主人可使用 /博客投稿列表 查看并审核投稿。"
                )
        else:
            yield (
                f"[WARNING] 投稿已保存到内存，但文件保存失败。插件重启后投稿可能丢失\n\n"
                f"投稿 ID: {submission_id}\n"
                f"标题: {title}\n"
                f"作者: {author_name or '匿名'}\n"
                f"提交时间: {submit_time}"
            )

    @filter.llm_tool(name="list_post_submissions")
    @require_admin
    async def list_post_submissions(self, event, status: str = ""):
        '''列出所有投稿。需要主人权限。

        可按状态过滤：不传参数返回全部，传 "pending" 仅返回待审核，
        传 "approved" 仅返回已批准，传 "rejected" 仅返回已拒绝。

        Args:
            status(string): 可选，按状态过滤投稿。可选值：pending / approved / rejected
        '''
        if not self._submissions_cache:
            yield "[INFO] 暂无投稿记录"
            return

        submissions = self._list_submissions(status)
        if not submissions:
            status_map = {"pending": "待审核", "approved": "已批准", "rejected": "已拒绝"}
            yield f"[INFO] 暂无{status_map.get(status, '')}的投稿"
            return

        stats = self._get_submission_stats()
        result = f"[INFO] 投稿列表（共 {stats['total']} 条 | 待审核 {stats['pending']} | 已批准 {stats['approved']} | 已拒绝 {stats['rejected']}）\n\n"
        for sub in submissions:
            status_emoji = {"pending": "⏳", "approved": "✅", "rejected": "❌"}
            result += f"{status_emoji.get(sub.status, '❓')} {sub.title}\n"
            result += f"   - 投稿 ID: `{sub.id}`\n"
            result += f"   - 作者: {sub.author_name or '匿名'}\n"
            result += f"   - 提交时间: {sub.submit_time}\n"
            status_text = {"pending": "待审核", "approved": "已批准", "rejected": "已拒绝"}.get(sub.status, sub.status)
            result += f"   - 状态: {status_text}\n"
            # AI 初审状态
            if sub.ai_review:
                ai_passed = sub.ai_review.get("passed")
                ai_score = sub.ai_review.get("score", "?")
                if ai_passed is True:
                    result += f"   - AI 初审: 建议过审 ({ai_score}/10)\n"
                elif ai_passed is False:
                    result += f"   - AI 初审: 建议打回 ({ai_score}/10)\n"
                else:
                    result += f"   - AI 初审: 异常，需人工审核\n"
            if sub.reject_reason:
                result += f"   - 拒绝原因: {sub.reject_reason}\n"
            result += "\n"

        yield result

    @filter.llm_tool(name="review_submission")
    @require_admin
    async def review_submission(self, event, submission_id: str):
        '''查看指定投稿的详细内容。需要主人权限。
        
        Args:
            submission_id(string): 投稿 ID
        '''
        submission = self._submissions_cache.get(submission_id)
        if not submission:
            yield f"[ERROR] 未找到投稿 ID: {submission_id}"
            return

        result = f"[INFO] 投稿详情 (ID: {submission_id})\n\n"
        result += f"标题: {submission['title']}\n"
        result += f"作者: {submission['author_name'] or '匿名'}\n"
        result += f"邮箱: {submission['author_email'] or '未提供'}\n"
        result += f"提交时间: {submission['submit_time']}\n"
        result += f"状态: {'待审核' if submission['status'] == 'pending' else '已批准' if submission['status'] == 'approved' else '已拒绝'}\n"
        result += f"分类: {submission['category'] or '未设置'}\n"
        result += f"标签: {submission['tags'] or '未设置'}\n"
        result += f"描述: {submission['description'] or '未设置'}\n"

        # 展示 AI 初审结果（如果存在）
        ai_review = submission.get("ai_review")
        if ai_review:
            result += self._format_ai_review_result(ai_review)
            result += "\n"

        result += f"\n正文内容:\n\n{submission['content']}\n"

        yield result

    @filter.llm_tool(name="approve_submission")
    @require_admin
    @require_blog_manager
    async def approve_submission(self, event, submission_id: str):
        '''批准指定投稿，将其发布到博客。需要主人权限。
        
        Args:
            submission_id(string): 投稿 ID
        '''
        submission = self._submissions_cache.get(submission_id)
        if not submission:
            yield f"[ERROR] 未找到投稿 ID: {submission_id}"
            return

        if submission["status"] != "pending":
            status_text = {"pending": "待审核", "approved": "已批准", "rejected": "已拒绝"}.get(
                submission["status"], submission["status"]
            )
            yield f"[ERROR] 投稿状态错误，当前状态: {status_text}"
            return

        tag_list = [t.strip() for t in submission["tags"].split(",") if t.strip()]
        extra: dict = {}
        if submission["category"]:
            extra["category"] = submission["category"]
        if submission["description"]:
            extra["description"] = submission["description"]
        if submission["author_name"]:
            extra["author"] = submission["author_name"]

        filename = FilenameUtil.resolve(submission["title"])

        if await self.blog_manager.exists(filename):
            yield f"[ERROR] 文章《{submission['title']}》已存在，无法发布"
            return

        metadata = PostMetadata(
            title=submission["title"],
            tags=tag_list,
            **extra
        )

        full_content = metadata.to_yaml() + "\n" + submission["content"]

        if await self.blog_manager.write_post(filename, full_content):
            submission["status"] = "approved"
            self._save_submissions()
            yield f"[OK] 投稿《{submission['title']}》已批准并发布\n\n提示：需要重新构建部署才能在网站上显示"
        else:
            yield f"[ERROR] 发布投稿《{submission['title']}》失败"

    @filter.llm_tool(name="reject_submission")
    @require_admin
    async def reject_submission(self, event, submission_id: str, reason: str = ""):
        '''拒绝指定投稿。需要主人权限。
        
        Args:
            submission_id(string): 投稿 ID
            reason(string): 拒绝原因（可选）
        '''
        submission = self._submissions_cache.get(submission_id)
        if not submission:
            yield f"[ERROR] 未找到投稿 ID: {submission_id}"
            return

        if submission["status"] != "pending":
            status_text = {"pending": "待审核", "approved": "已批准", "rejected": "已拒绝"}.get(
                submission["status"], submission["status"]
            )
            yield f"[ERROR] 投稿状态错误，当前状态: {status_text}"
            return

        submission["status"] = "rejected"
        submission["reject_reason"] = reason
        self._save_submissions()

        result = f"[INFO] 投稿《{submission['title']}》已拒绝\n"
        if reason:
            result += f"拒绝原因: {reason}\n"
        result += "\n提示：该投稿仍保留在列表中，可稍后重新审核"

        yield result

    @filter.llm_tool(name="delete_submission")
    @require_admin
    async def delete_submission(self, event, submission_id: str):
        '''删除指定投稿记录。需要主人权限。

        用于清理已处理（已批准/已拒绝）的投稿记录，或删除错误提交的草稿。
        被删除的投稿将永久移除，不可恢复。

        Args:
            submission_id(string): 投稿 ID
        '''
        submission = self._submissions_cache.get(submission_id)
        if not submission:
            yield f"[ERROR] 未找到投稿 ID: {submission_id}"
            return

        title = submission.get("title", "未知")
        del self._submissions_cache[submission_id]
        self._save_submissions()

        yield f"[OK] 投稿《{title}》(ID: {submission_id}) 已永久删除"

    @filter.llm_tool(name="retract_submission")
    async def retract_submission(self, event, submission_id: str):
        '''撤回自己的投稿。仅投稿者本人可以撤回。

        只能撤回状态为"待审核"的投稿，已批准或已拒绝的投稿无法撤回。

        Args:
            submission_id(string): 投稿 ID
        '''
        submission = self._submissions_cache.get(submission_id)
        if not submission:
            yield f"[ERROR] 未找到投稿 ID: {submission_id}"
            return

        if submission.get("status") != "pending":
            status_text = {"pending": "待审核", "approved": "已批准", "rejected": "已拒绝"}.get(
                submission.get("status"), submission.get("status")
            )
            yield f"[ERROR] 投稿状态为「{status_text}」，只有待审核的投稿才能撤回"
            return

        # 验证投稿者身份
        user_id = (
            getattr(event, 'user_id', None) or
            getattr(event, 'sender_id', None) or
            getattr(event, 'from_id', None) or
            getattr(event, 'user_id_holder', None)
        )
        sub_user_id = submission.get("user_id")
        if user_id is not None and sub_user_id is not None:
            if str(user_id) != str(sub_user_id):
                yield f"[ERROR] 您只能撤回自己的投稿。该投稿由用户 {sub_user_id} 提交"
                return

        title = submission.get("title", "未知")
        del self._submissions_cache[submission_id]
        self._save_submissions()

        yield f"[OK] 投稿《{title}》(ID: {submission_id}) 已撤回"

    @filter.llm_tool(name="ai_review_submission")
    @require_admin
    async def ai_review_submission(self, event, submission_id: str):
        '''手动触发对指定投稿的 AI 初审评估。需要主人权限。

        当 AI 初审未启用或需要重新评估时，可使用此工具手动触发 AI 初审。
        初审结果会保存到投稿记录中，并可通过 review_submission 查看。

        Args:
            submission_id(string): 投稿 ID
        '''
        submission = self._submissions_cache.get(submission_id)
        if not submission:
            yield f"[ERROR] 未找到投稿 ID: {submission_id}"
            return

        yield f"正在对投稿《{submission.get('title', '未知')}》进行 AI 初审，请稍候……"

        review_result = await self._perform_ai_review(submission)
        self._store_ai_review_result(submission_id, review_result)

        review_text = self._format_ai_review_result(review_result)
        if review_text:
            yield review_text
        else:
            yield "[ERROR] AI 初审未能生成有效结果，请稍后重试或进行人工审核。"

    @filter.llm_tool(name="submission_stats")
    @require_admin
    async def submission_stats(self, event):
        '''获取投稿统计概览。需要主人权限。

        返回待审核、已批准、已拒绝的投稿数量汇总。
        '''
        if not self._submissions_cache:
            yield "[INFO] 暂无投稿记录"
            return

        stats = self._get_submission_stats()
        result = [
            "📊 **投稿统计**",
            "",
            f"| 状态 | 数量 |",
            f"|------|------|",
            f"| ⏳ 待审核 | {stats['pending']} |",
            f"| ✅ 已批准 | {stats['approved']} |",
            f"| ❌ 已拒绝 | {stats['rejected']} |",
            f"| **合计** | **{stats['total']}** |",
        ]
        yield "\n".join(result)

    # ========================================================================
    # 进阶语法 LLM 工具
    # ========================================================================

    def _get_enabled_syntax_features(self) -> dict:
        """获取当前启用的进阶语法功能列表

        根据配置项 enable_advanced_syntax 和各子项开关，返回已启用的功能元数据。
        """
        # 主开关关闭时，不返回任何功能
        if not self.config.get("enable_advanced_syntax", True):
            return {}

        enabled = {}
        for feat_id, feat in ADVANCED_SYNTAX_FEATURES.items():
            if self.config.get(feat["key"], True):
                enabled[feat_id] = feat
        return enabled

    def _format_syntax_menu(self, enabled: dict) -> str:
        """格式化进阶语法功能菜单

        Args:
            enabled: 已启用的功能字典

        Returns:
            格式化的功能菜单文本
        """
        if not enabled:
            return "[INFO] 进阶语法功能未启用。请在插件配置中开启 `enable_advanced_syntax` 开关。"

        lines = ["📋 **Firefly 博客进阶语法功能菜单**\n"]

        # 按分类分组
        by_category: dict = {}
        for feat_id, feat in enabled.items():
            cat = feat["category"]
            if cat not in by_category:
                by_category[cat] = []
            by_category[cat].append((feat_id, feat))

        for cat, items in by_category.items():
            lines.append(f"### {cat}")
            for feat_id, feat in items:
                disabled = not self.config.get(feat["key"], True)
                status = "✅" if not disabled else "⏸️"
                lines.append(f"\n**{status} {feat['name']}** (`{feat_id}`)")
                lines.append(f"  语法: `{feat['syntax']}`")
                lines.append(f"  ✅ 优点: {'; '.join(feat['pros'][:2])}")
                lines.append(f"  ⚠️ 缺点: {'; '.join(feat['cons'][:2])}")
            lines.append("")

        lines.append("---")
        lines.append("使用 `get_syntax_guide` 工具可获取特定语法的详细使用指南。")
        return "\n".join(lines)

    @filter.llm_tool(name="list_advanced_syntax")
    async def list_advanced_syntax(self, event):
        '''列出 Firefly 博客支持的所有进阶 Markdown 语法功能。

        返回当前已启用的进阶语法功能菜单，包含每个功能的名称、语法示例、
        优点和缺点。用户可根据此菜单选择适合的语法增强文章表现力。

        如果主开关 `enable_advanced_syntax` 未开启，此工具将返回提示信息。
        '''
        enabled = self._get_enabled_syntax_features()
        yield self._format_syntax_menu(enabled)

    @filter.llm_tool(name="get_syntax_guide")
    async def get_syntax_guide(self, event, syntax_id: str):
        '''获取指定进阶语法的详细使用指南。

        Args:
            syntax_id(string): 语法功能 ID。可选值：github_card（GitHub 仓库卡片）、
                admonitions（提醒框）、spoiler（剧透文本）、image_grid（图片画廊网格）、
                code_blocks（代码块进阶）、mermaid（Mermaid 图表）、
                plantuml（PlantUML 图表）、katex（KaTeX 数学公式）

        Returns:
            包含语法说明、示例代码和详细使用指南的完整文档。
            如果该语法功能未启用，会返回提示信息。
        '''
        if not self.config.get("enable_advanced_syntax", True):
            yield "[INFO] 进阶语法功能未启用，请在插件配置中开启 `enable_advanced_syntax` 开关。"
            return

        feat = ADVANCED_SYNTAX_FEATURES.get(syntax_id)
        if not feat:
            valid_ids = ", ".join(f"`{k}`" for k in ADVANCED_SYNTAX_FEATURES)
            yield f"[ERROR] 未知的语法 ID `{syntax_id}`。可选值：{valid_ids}"
            return

        if not self.config.get(feat["key"], True):
            yield f"[INFO] 语法功能「{feat['name']}」当前未启用。请在插件配置中开启 `{feat['key']}` 开关。"
            return

        lines = [
            f"## {feat['name']} 使用指南",
            f"",
            f"**分类**: {feat['category']}",
            f"",
            f"### 优点",
        ]
        for p in feat["pros"]:
            lines.append(f"- ✅ {p}")

        lines.append("")
        lines.append("### 缺点")
        for c in feat["cons"]:
            lines.append(f"- ⚠️ {c}")

        lines.append("")
        lines.append("### 基本语法")
        lines.append(f"```\n{feat['syntax']}\n```")

        lines.append("")
        lines.append("### 示例")
        lines.append(f"```\n{feat['example']}\n```")

        lines.append("")
        lines.append("### 详细指南")
        lines.append(feat["guide"])

        yield "\n".join(lines)

    # ========================================================================
    # 显式指令注册（用户可直接使用的命令）
    # ========================================================================

    @filter.command("博客列表", alias=["博客文章", "列出文章"], priority=5)
    @require_blog_manager
    async def cmd_list_posts(self, event):
        """列出所有博客文章"""
        posts = await self.blog_manager.list_posts()
        yield event.plain_result(self._format_post_list(posts))

    @filter.command("博客搜索", alias=["搜索文章"], priority=5)
    @require_blog_manager
    async def cmd_search_posts(self, event, keyword: str):
        """搜索博客文章

        Args:
            keyword(string): 搜索关键词，按标题/分类/标签匹配
        """
        posts = await self.blog_manager.list_posts()
        results = []
        keyword_lower = keyword.lower()
        for post in posts:
            if keyword_lower in post.title.lower() or keyword_lower in post.category.lower() or any(keyword_lower in tag.lower() for tag in post.tags):
                results.append(post)
        if not results:
            yield event.plain_result(f"[INFO] 未找到包含「{keyword}」的文章")
        else:
            yield event.plain_result(self._format_post_list(results))

    @filter.command("博客投稿", alias=["提交投稿", "投稿文章"], priority=5)
    @require_blog_manager
    async def cmd_submit_post(self, event, title: str = ""):
        """提交文章投稿。

        Args:
            title(string): 文章标题，正文内容通过后续消息或分号分隔提供

        用法: /博客投稿 标题;正文内容
        或者: /博客投稿 标题
               正文内容（换行输入）

        所有用户均可使用此命令提交投稿，管理员审核后决定是否发布。
        """
        if not title:
            # 尝试从消息中提取标题
            message = getattr(event, 'message', '') or ''
            if not message.strip():
                yield event.plain_result(
                    "[ERROR] 请提供投稿标题和内容。\n"
                    "用法: /博客投稿 标题\n"
                    "     正文内容"
                )
                return
            # 尝试解析：第一行作为标题
            lines = message.strip().split('\n', 1)
            title = lines[0].strip()
            content = lines[1].strip() if len(lines) > 1 else ""
            if not title:
                yield event.plain_result("[ERROR] 请提供投稿标题")
                return
        else:
            # title 参数由命令解析提供，正文需从消息中提取
            message = getattr(event, 'message', '') or ''
            # 去掉命令前缀，找到 title 后的内容
            parts = message.split(title, 1)
            content = parts[1].strip() if len(parts) > 1 else ""

        if not content:
            yield event.plain_result(
                "[ERROR] 请提供投稿内容。\n"
                "用法: /博客投稿 标题\n"
                "     正文内容（支持 Markdown 语法）"
            )
            return

        import uuid
        import time

        # 获取用户信息
        author_name = (
            getattr(event, 'sender_name', None) or
            getattr(event, 'user_name', None) or
            getattr(event, 'nickname', None) or "匿名用户"
        )
        user_id = (
            getattr(event, 'user_id', None) or
            getattr(event, 'sender_id', None) or
            getattr(event, 'from_id', None) or
            getattr(event, 'user_id_holder', None) or ""
        )

        submission_id = uuid.uuid4().hex[:12]
        sub = {
            "id": submission_id,
            "title": title.strip(),
            "content": content.strip(),
            "author_name": str(author_name),
            "user_id": str(user_id) if user_id else "",
            "submit_time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "status": "pending",
            "reject_reason": "",
        }
        self._submissions_cache[submission_id] = sub
        self._save_submissions()

        yield event.plain_result(
            f"[OK] 投稿已提交！\n"
            f"   - 投稿 ID: `{submission_id}`\n"
            f"   - 标题: {title.strip()}\n"
            f"   - 状态: 待审核\n"
            f"   - 使用 `/我的投稿` 查看您的投稿状态\n"
            f"   - 管理员将在审核后决定是否发布"
        )

    @filter.command("我的投稿", alias=["我的提交", "投稿状态"], priority=5)
    async def cmd_my_submissions(self, event):
        """查看自己的投稿记录和状态"""
        user_id = (
            getattr(event, 'user_id', None) or
            getattr(event, 'sender_id', None) or
            getattr(event, 'from_id', None) or
            getattr(event, 'user_id_holder', None) or ""
        )
        user_id = str(user_id) if user_id else ""

        if not user_id:
            yield event.plain_result("[ERROR] 无法识别您的用户身份，请稍后再试")
            return

        my_subs = [
            Submission.from_dict(s) for s in self._submissions_cache.values()
            if str(s.get("user_id", "")) == user_id
        ]
        if not my_subs:
            yield event.plain_result(
                "[INFO] 您还没有提交过投稿。\n"
                "使用 `/博客投稿 标题` 来提交您的第一篇文章。"
            )
            return

        my_subs.sort(key=lambda x: x.submit_time, reverse=True)
        status_emoji = {"pending": "⏳", "approved": "✅", "rejected": "❌"}
        status_text = {"pending": "待审核", "approved": "已批准", "rejected": "已拒绝"}

        lines = ["📋 **我的投稿记录**", ""]
        for sub in my_subs:
            emoji = status_emoji.get(sub.status, "❓")
            text = status_text.get(sub.status, sub.status)
            lines.append(f"{emoji} {sub.title}")
            lines.append(f"   ID: `{sub.id}` | 状态: {text} | 提交: {sub.submit_time}")
            if sub.reject_reason:
                lines.append(f"   ⚠️ 拒绝原因: {sub.reject_reason}")
            lines.append("")

        # 统计
        stats = {"pending": 0, "approved": 0, "rejected": 0}
        for sub in my_subs:
            if sub.status in stats:
                stats[sub.status] += 1
        lines.append(f"---")
        lines.append(f"汇总: {stats['pending']} 篇待审核 | {stats['approved']} 篇已发布 | {stats['rejected']} 篇已拒绝")

        yield event.plain_result("\n".join(lines))

    @filter.command("博客环境", alias=["检查环境"], priority=5)
    @require_build_manager
    async def cmd_check_env(self, event):
        """检查博客构建环境"""
        ok, msg = await self.build_manager.check_environment()
        prefix = "[OK]" if ok else "[ERROR]"
        yield event.plain_result(f"{prefix} {msg}")

    @filter.command("博客构建", alias=["构建博客"], priority=10)
    @require_admin
    @require_build_manager
    async def cmd_build_blog(self, event):
        """构建博客（仅管理员可用）"""
        ok, msg = await self.build_manager.build()
        prefix = "[OK]" if ok else "[ERROR]"
        yield event.plain_result(f"{prefix} {msg}")

    @filter.command("博客部署", alias=["部署博客"], priority=10)
    @require_admin
    @require_build_manager
    async def cmd_deploy_blog(self, event):
        """部署博客到服务器（仅管理员可用）"""
        ok, msg = await self.build_manager.deploy()
        prefix = "[OK]" if ok else "[ERROR]"
        yield event.plain_result(f"{prefix} {msg}")

    @filter.command("博客投稿列表", alias=["投稿列表", "待审核投稿"], priority=5)
    @require_admin
    async def cmd_list_submissions(self, event):
        """查看投稿列表（仅管理员可用）"""
        if not self._submissions_cache:
            yield event.plain_result("[INFO] 暂无待审核的投稿")
            return
        submissions = sorted(self._submissions_cache.values(), key=lambda x: x["submit_time"], reverse=True)
        result = "[INFO] 投稿列表:\n"
        for sub in submissions:
            status_map = {"pending": "[PENDING]", "approved": "[APPROVED]", "rejected": "[REJECTED]"}
            status_str = status_map.get(sub["status"], "[UNKNOWN]")
            author = sub["author_name"] or "匿名"
            result += f"{status_str} {sub['title']} - {author} - {sub['submit_time']}\n"
        yield event.plain_result(result)

    @filter.command("内存状态", alias=["检查内存"], priority=5)
    @require_admin
    async def cmd_memory_status(self, event):
        """检查当前内存状态（公开命令）"""
        ok, msg = self._check_memory_status()
        yield event.plain_result(msg)

    @filter.command("博客帮助", alias=["博客菜单", "博客命令", "firefly帮助", "firefly"], priority=1)
    async def cmd_help(self, event):
        """显示博客管理帮助菜单，列出所有可用命令和功能"""
        has_admin = self.config.get("admin_umo", "").strip() != ""
        is_admin = False
        if has_admin:
            ok, _ = self._check_admin_permission(event)
            is_admin = ok

        deploy_mode = self.config.get("deploy_mode", "local_build")
        mode_map = {
            "local_build": "本地构建 → SSH 部署到远端",
            "remote_build": "远端服务器直接构建",
            "local_only": "纯本地（博客与 AstrBot 同机）",
        }
        mode_desc = mode_map.get(deploy_mode, deploy_mode)

        lines = [
            f"🔥 **Firefly 博客管理插件 v1.3.5**",
            "",
            f"部署模式: `{deploy_mode}` ({mode_desc})",
            "",
            "---",
            "",
        ]

        # ═══════════════════════════════════════════
        # 一、 显式命令
        # ═══════════════════════════════════════════
        lines.append("## ⌨️ 显式命令")
        lines.append("")

        # ── 公开命令 ──
        lines.append("### 📖 公开命令（所有人可用）")
        lines.append("")
        pub_cmds = [
            ("/博客帮助", "博客菜单, 博客命令, firefly帮助, firefly", "显示此帮助菜单"),
            ("/博客列表", "博客文章, 列出文章", "列出所有已发布的博客文章"),
            ("/博客搜索 <关键词>", "搜索文章", "按标题/分类/标签搜索文章"),
            ("/博客投稿 <标题>", "提交投稿, 投稿文章", "提交文章投稿草稿，等待管理员审核"),
            ("/我的投稿", "我的提交, 投稿状态", "查看自己提交的投稿及其审核状态"),
            ("/博客环境", "检查环境", "检查博客构建环境（Node.js、pnpm）是否就绪"),
            ("/内存状态", "检查内存", "查看服务器当前内存使用情况"),
        ]
        for cmd, alias, desc in pub_cmds:
            lines.append(f"| `{cmd}` | {desc} |")
            if alias:
                lines.append(f"| ↳ 别名 | `{alias}` |")
        lines.append("")

        # ── 管理员命令 ──
        lines.append("### 🔒 管理员命令（仅管理员可用）")
        if has_admin and not is_admin:
            lines.append("> ⚠️ 当前用户不是管理员，以下命令不可用。")
        lines.append("")
        adm_cmds = [
            ("/博客构建", "构建博客", "执行博客构建"),
            ("/博客部署", "部署博客", "将构建产物部署到 Web 服务器"),
            ("/博客投稿列表", "投稿列表, 待审核投稿", "列出所有待审核的文章投稿"),
        ]
        for cmd, alias, desc in adm_cmds:
            lines.append(f"| `{cmd}` | {desc} |")
            if alias:
                lines.append(f"| ↳ 别名 | `{alias}` |")
        lines.append("")

        lines.append("---")
        lines.append("")

        # ═══════════════════════════════════════════
        # 二、 LLM 工具（AI 自然语言能力）
        # ═══════════════════════════════════════════
        lines.append("## 🤖 AI 自然语言能力（LLM 工具）")
        lines.append("")
        lines.append("以下功能无需记忆命令，直接用自然语言对 AI 描述即可。")
        lines.append("")

        # ── 公开工具 ──
        lines.append("### 📖 公开工具（所有人可用）")
        lines.append("")
        pub_tools = [
            ("list_blog_posts", "列出所有文章"),
            ("get_blog_post", "获取指定文章的完整内容"),
            ("search_blog_posts", "按关键词搜索文章"),
            ("check_blog_environment", "检查博客构建环境是否就绪"),
            ("check_memory_status", "检查当前系统内存状态"),
            ("check_build_resource", "检查构建所需资源（磁盘+内存）"),
            ("get_build_config", "查看当前构建配置（内存阈值、并发等）"),
            ("submit_post_draft", "提交文章投稿草稿"),
            ("retract_submission", "撤回自己提交的待审核投稿"),
            ("list_advanced_syntax", "列出所有可用的进阶 Markdown 语法"),
            ("get_syntax_guide", "获取指定进阶语法的详细使用指南"),
        ]
        for tool_name, desc in pub_tools:
            lines.append(f"| `{tool_name}` | {desc} |")
        lines.append("")

        # ── 管理员工具 ──
        lines.append("### 🔒 管理员工具（仅管理员可用）")
        if has_admin and not is_admin:
            lines.append("> ⚠️ 当前用户不是管理员，以下工具不可用。")
        lines.append("")
        adm_tools = [
            ("create_blog_post", "创建新文章（支持标签、分类、草稿、置顶等）"),
            ("delete_blog_post", "删除指定文章"),
            ("update_blog_post", "更新现有文章的内容或元数据"),
            ("install_blog_dependencies", "安装博客构建所需的 npm 依赖"),
            ("build_blog", "执行博客构建"),
            ("deploy_blog", "将构建产物部署到 Web 服务器"),
            ("auto_setup_blog", "一键自动检测环境并完成构建部署"),
            ("build_and_deploy_blog", "一键构建并部署（连招）"),
            ("list_post_submissions", "列出所有投稿，支持按状态过滤"),
            ("review_submission", "查看指定投稿的详细内容"),
            ("approve_submission", "批准投稿并发布为正式文章"),
            ("reject_submission", "拒绝投稿（可附带拒绝原因）"),
            ("delete_submission", "删除指定投稿记录（永久删除）"),
            ("submission_stats", "查看投稿统计概览"),
            ("ai_review_submission", "手动触发对指定投稿的 AI 初审评估"),
        ]
        for tool_name, desc in adm_tools:
            lines.append(f"| `{tool_name}` | {desc} |")
        lines.append("")

        lines.append("---")
        lines.append("")

        # ═══════════════════════════════════════════
        # 三、 进阶语法状态
        # ═══════════════════════════════════════════
        if self.config.get("enable_advanced_syntax", True):
            lines.append("## 🎨 进阶语法（当前已启用）")
            lines.append("")
            by_cat: dict = {}
            for fid, feat in ADVANCED_SYNTAX_FEATURES.items():
                if self.config.get(feat["key"], True):
                    cat = feat["category"]
                    by_cat.setdefault(cat, []).append(feat["name"])
            for cat, names in by_cat.items():
                lines.append(f"- **{cat}**: {', '.join(names)}")
            lines.append("")
            lines.append("> 对 AI 说「有哪些进阶语法」或「教我写提醒框」即可获取详细指南。")
        else:
            lines.append("## 🎨 进阶语法（未启用）")
            lines.append("")
            lines.append("> 进阶语法功能未开启。在插件配置中打开 `enable_advanced_syntax` 即可使用。")
        lines.append("")

        lines.append("---")
        lines.append("")

        # ═══════════════════════════════════════════
        # 四、 AI 初审状态
        # ═══════════════════════════════════════════
        if self.config.get("enable_ai_review", True):
            lines.append("## AI 初审（当前已启用）")
            lines.append("")
            lines.append("AI 初审会在每次有新投稿提交时自动运行，从内容质量、格式规范、")
            lines.append("完整性等维度评估文章，并给出过审/打回建议。")
            lines.append("")
            lines.append("- 投稿列表中会展示每篇投稿的 AI 初审状态")
            lines.append("- 查看投稿详情时可以看到完整的 AI 评估结果")
            lines.append("- 管理员可使用 `ai_review_submission` 手动重新触发 AI 初审")
            lines.append("")
        else:
            lines.append("## AI 初审（未启用）")
            lines.append("")
            lines.append("> AI 初审功能未开启。在插件配置中打开 `enable_ai_review` 即可使用。")
            lines.append("> 开启后，新投稿提交时会自动调用 AI 进行内容质量评估。")
        lines.append("")

        lines.append("---")
        lines.append("")

        # ═══════════════════════════════════════════
        # 五、 快速上手
        # ═══════════════════════════════════════════
        lines.append("## 💡 快速上手")
        lines.append("")
        lines.append("试试对 AI 说这些话：")
        lines.append("")
        lines.append('- "帮我创建一篇关于 Docker 入门的文章"')
        lines.append('- "列出博客上所有文章"')
        lines.append('- "帮我检查一下博客环境是否正常"')
        lines.append('- "构建并部署博客"')
        lines.append('- "帮我看看有哪些投稿需要审核"')
        lines.append('- "帮我提交一篇投稿"')
        lines.append('- "查看我的投稿状态"')
        if self.config.get("enable_advanced_syntax", True):
            lines.append('- "有哪些进阶语法可以用？"')
            lines.append('- "教我写一个 Mermaid 流程图"')

        yield event.plain_result("\n".join(lines))

    # ========================================================================
    # 生命周期管理
    # ========================================================================

    async def terminate(self):
        """插件卸载/停用时清理资源"""
        # 保存投稿缓存
        if self._submissions_cache:
            self._save_submissions()
            logger.info(f"[Firefly] 已保存 {len(self._submissions_cache)} 条投稿数据")
        
        # 关闭 SSH 连接
        if self.remote_executor:
            try:
                await self.remote_executor.close()
            except Exception as e:
                logger.error(f"[Firefly] 关闭 SSH 连接失败: {e}")
            finally:
                self.remote_executor = None
        
        logger.info("[Firefly] 插件资源已清理")
