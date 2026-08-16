"""
AstrBot Firefly 博客管理插件 v1.10.0

通过 AI 指令管理 Firefly 博客的文章和部署。
支持三种部署模式：
1. 本地构建模式 (local_build) - 在 AstrBot 所在服务器构建，通过 SSH 部署到远端
2. 远程构建模式 (remote_build) - 通过 SSH 在远端服务器直接构建部署
3. 纯本地模式 (local_only) - 博客和 AstrBot 在同一台机器

Firefly 博客基于 Astro 框架，文章以 Markdown 文件形式存储，
使用 YAML Front-matter 定义文章元数据。
构建产物位于 dist/ 目录，需部署到 Web 服务器。

架构说明：
- 插件入口文件，仅包含插件主类（Star）、命令处理器和 LLM 工具
- 核心逻辑按职责拆分到子模块：models / constants / common / executors /
  filesystem / blog_manager / build_deploy
- 通过依赖注入实现模式切换，核心逻辑与具体实现解耦
- 所有工具函数均支持异步操作，避免阻塞主线程
- 配置采用声明式定义，支持 WebUI 可视化配置
"""

from __future__ import annotations

import json
import os
import posixpath
import re
import shlex
import shutil
import time
import uuid
from datetime import datetime
from typing import List, Optional

try:
    import psutil
except ImportError:
    psutil = None

from astrbot.api import logger, AstrBotConfig
from astrbot.api.event import filter
from astrbot.api.star import Context, Star, register

from .blog_manager import BlogManager
from .build_deploy import BuildDeployManager
from .common import (
    get_user_id,
    is_firefly_blog_local,
    parse_deploy_mode,
    require_admin,
    require_blog_manager,
    require_build_manager,
)
from .constants import (
    ADVANCED_SYNTAX_FEATURES,
    DEFAULT_BLOG_ROOT,
    PLUGIN_VERSION,
)
from .executors import LocalExecutor, RemoteExecutor
from .filesystem import FileSystem, FilenameUtil, LocalFileSystem, RemoteFileSystem
from .models import (
    SUBMISSION_STATUS_BADGE,
    SUBMISSION_STATUS_EMOJI,
    SUBMISSION_STATUS_TEXT,
    DeployMode,
    PostInfo,
    PostMetadata,
    Submission,
)
from .webui import WebUIMixin


# ============================================================================
# 插件主类
# ============================================================================

@register(
    "astrbot_plugin_Firefly_Blog_Manager",
    "月凌",
    "通过 AI 指令管理 Firefly 博客文章和部署",
    PLUGIN_VERSION,
    "https://github.com/qiyueling2716/astrbot_plugin_Firefly_Blog_Manager",
)
class FireflyBlogManager(WebUIMixin, Star):
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
        # 注册 Dashboard 页面 Web API
        self.register_web_apis()
        
        # 安全最佳实践：未配置 admin_umo 时发出警告
        if not self.config.get("admin_umo", "").strip():
            logger.warning("[Firefly] 未配置管理员 UMO，所有管理操作无需权限验证。请在生产环境中设置 admin_umo。")
        
        # 投稿持久化配置
        self._submissions_file = self._get_submissions_file()
        self._submissions_cache = self._load_submissions()

    def _get_backup_dir(self) -> str:
        try:
            from astrbot.core.star.star_tools import StarTools
            data_dir = str(StarTools.get_data_dir())
            backup_dir = os.path.join(data_dir, "backups")
        except Exception:
            backup_dir = os.path.join(self.plugin_dir, "backups")
        os.makedirs(backup_dir, exist_ok=True)
        return backup_dir

    def _get_submissions_file(self) -> str:
        """获取投稿缓存文件路径

        使用 AstrBot 框架标准接口 StarTools.get_data_dir() 将数据保存在
        数据目录（data/plugin_data/），避免将运行时数据写入插件目录。
        StarTools 不可用时回退到插件目录，并迁移旧文件。
        """
        try:
            from astrbot.core.star.star_tools import StarTools

            data_dir = StarTools.get_data_dir()
            data_path = os.path.join(str(data_dir), "submissions_cache.json")
        except Exception as e:
            logger.warning(f"[Firefly] 无法获取 AstrBot 数据目录，回退到插件目录: {e}")
            data_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "_submissions_cache.json",
            )

        # 迁移旧版缓存文件（位于插件目录）
        legacy_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "_submissions_cache.json",
        )
        if os.path.exists(legacy_path) and legacy_path != data_path and not os.path.exists(data_path):
            try:
                shutil.copy2(legacy_path, data_path)
                logger.info(f"[Firefly] 已迁移投稿缓存: {legacy_path} -> {data_path}")
            except Exception as e:
                logger.warning(f"[Firefly] 迁移投稿缓存失败: {e}")

        return data_path
    
    def _load_submissions(self) -> dict:
        """从文件加载投稿缓存"""
        try:
            if os.path.exists(self._submissions_file):
                with open(self._submissions_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            logger.warning(f"[Firefly] 加载投稿缓存失败: {e}")
        return {}
    
    _SUBMISSIONS_CACHE_MAX = 500  # 投稿缓存最大条目数

    def _save_submissions(self) -> bool:
        """保存投稿缓存到文件，超限自动淘汰最旧的 rejected"""
        try:
            # 超限淘汰：优先淘汰 rejected，其次 oldest
            while len(self._submissions_cache) > self._SUBMISSIONS_CACHE_MAX:
                rejected = [
                    (sid, s) for sid, s in self._submissions_cache.items()
                    if s.get("status") == "rejected"
                ]
                if rejected:
                    oldest_rejected = min(rejected, key=lambda x: x[1].get("submit_time", ""))
                    del self._submissions_cache[oldest_rejected[0]]
                else:
                    oldest = min(self._submissions_cache.items(), key=lambda x: x[1].get("submit_time", ""))
                    del self._submissions_cache[oldest[0]]
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
            json_match = re.search(r'\{[\s\S]*\}', resp_text)
            if json_match:
                result = json.loads(json_match.group())
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
        """检查用户是否有管理员权限

        权限判定顺序（使用 AstrBot 框架标准接口）：
        1. 优先使用框架提供的平台管理员角色（event.is_admin()）
        2. 回退到配置文件中的 admin_umo 匹配

        Args:
            event: 事件对象，包含角色/UMO 信息

        返回: (是否有权限, 错误消息或空字符串)

        admin_umo 支持两种配置格式：
        1. 完整 UMO 格式：platform:user_id（如 onebot:123456789）
        2. 仅用户 ID：纯数字（如 123456789），会自动匹配任意平台
        """
        admin_umo = self.config.get("admin_umo", "").strip()
        
        # 如果未配置管理员 UMO，允许所有操作（方便调试）
        if not admin_umo:
            return True, ""
        
        # 优先使用框架标准接口：平台管理员角色
        is_admin = getattr(event, "is_admin", None)
        if callable(is_admin):
            try:
                if is_admin():
                    return True, ""
            except Exception:
                logger.warning("[Firefly] 调用 event.is_admin() 失败，回退到 UMO 匹配")
        
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
        if psutil is None:
            logger.warning("[Firefly] psutil 未安装，跳过内存检查")
            return True, "资源检查：psutil 未安装，跳过内存检查"
        try:
            mem = psutil.virtual_memory()
            available_mb = mem.available / (1024 ** 2)
            total_mb = mem.total / (1024 ** 2)
            used_percent = mem.percent
            
            if available_mb < memory_threshold:
                return False, f"内存不足，仅剩余 {available_mb:.2f} MB（总内存 {total_mb:.0f} MB，使用率 {used_percent:.1f}%）。构建 Firefly 博客需要约 1.5GB 内存，建议设置 build_memory_threshold 为更低的值，或使用 remote_build 模式让远端服务器承担构建工作。"
            
            logger.info(f"[Firefly] 内存检查通过: {available_mb:.2f} MB 可用")
            return True, f"资源充足。可用内存: {available_mb:.2f} MB（总内存 {total_mb:.0f} MB，使用率 {used_percent:.1f}%）"
        except Exception as e:
            logger.error(f"[Firefly] 内存检查失败: {e}")
            return True, "资源检查：内存检查失败，继续执行"

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

    
    def _check_memory_status(self) -> str:
        """检查当前内存状态，返回详细信息"""
        if psutil is None:
            return "[ERROR] psutil 未安装，无法检查内存状态。请安装 psutil: pip install psutil"
        try:
            mem = psutil.virtual_memory()
            available_mb = mem.available / (1024 ** 2)
            total_mb = mem.total / (1024 ** 2)
            used_percent = mem.percent
            memory_threshold = self.config.get("build_memory_threshold", 1536)
            
            status = "[OK]" if available_mb >= memory_threshold else "[WARNING]"
            return f"{status} 当前内存状态:\n- 总内存: {total_mb:.0f} MB\n- 可用内存: {available_mb:.2f} MB\n- 使用率: {used_percent:.1f}%\n- 构建阈值: {memory_threshold} MB\n- 是否满足构建条件: {'是' if available_mb >= memory_threshold else '否'}"
        except Exception as e:
            logger.error(f"[Firefly] 内存检查失败: {e}")
            return "[ERROR] 内存检查失败，请查看日志获取详细信息"

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
            if is_firefly_blog_local(path):
                is_built = self._is_blog_built(path)
                logger.info(f"[Firefly] 自动检测到博客目录: {path} (已构建: {is_built})")
                return path, is_built, True

        # 未找到，返回默认值
        return DEFAULT_BLOG_ROOT, False, False

    def _init_components(self):
        """根据配置初始化文件系统、博客管理器和构建部署管理器"""
        deploy_mode = parse_deploy_mode(self.config.get("deploy_mode", "local_build"))
        
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
        self.src_root = os.path.join(blog_root, "src")

        if deploy_mode == DeployMode.REMOTE_BUILD:
            # 远程构建模式：文章操作和构建都在远端
            self.remote_executor = RemoteExecutor(self.config)
            self.fs = RemoteFileSystem(self.remote_executor)
            remote_blog_root = self.config.get("remote_blog_root", "/var/www/firefly")
            remote_posts_dir = posixpath.join(remote_blog_root, "src", "content", "posts")
            self.src_root = posixpath.join(remote_blog_root, "src")
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

        # 删除前备份
        old_content = await self.blog_manager.read_post(filename)
        if old_content:
            backup_dir = self._get_backup_dir()
            os.makedirs(backup_dir, exist_ok=True)
            backup_path = os.path.join(backup_dir, f"{filename}.{datetime.now().strftime('%Y%m%d%H%M%S')}.bak")
            try:
                with open(backup_path, 'w', encoding='utf-8') as f:
                    f.write(old_content)
                # 清理旧备份：每个文件最多保留 5 个
                backups = sorted(
                    [f for f in os.listdir(backup_dir) if f.startswith(filename) and f.endswith('.bak')],
                    reverse=True,
                )
                for old_bak in backups[5:]:
                    try:
                        os.remove(os.path.join(backup_dir, old_bak))
                    except OSError:
                        pass
            except Exception as e:
                logger.warning(f"[Firefly] 备份文章 {filename} 失败: {e}")

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
        draft: bool = None,
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
            draft(boolean): 是否设为草稿，为None则不修改
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
            if draft is not None:
                metadata.draft = draft

            new_full_content = metadata.to_yaml() + "\n" + body

            # 先写新文件，成功后再删旧文件（原子操作）
            if not await self.blog_manager.write_post(new_filename, new_full_content):
                yield "[ERROR] 重命名失败：无法写入新文件"
                return
            if not await self.blog_manager.delete_post(old_filename):
                # 回滚：删除新写入的文件
                try:
                    await self.blog_manager.delete_post(new_filename)
                except Exception:
                    pass
                yield "[ERROR] 重命名失败：无法删除旧文件，已回滚"
                return
            yield f"[OK] 文章已重命名为《{new_title}》\n提示：需要重新构建部署才能生效"
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
        if draft is not None:
            metadata.draft = draft
        if image:
            metadata.image = image
        if author:
            metadata.author = author
        if comment is not None:
            metadata.comment = comment

        backup_dir = self._get_backup_dir()
        os.makedirs(backup_dir, exist_ok=True)
        backup_path = os.path.join(backup_dir, f"{old_filename}.{datetime.now().strftime('%Y%m%d%H%M%S')}.bak")
        try:
            with open(backup_path, 'w', encoding='utf-8') as f:
                f.write(old_content)
            backups = sorted(
                [f for f in os.listdir(backup_dir) if f.startswith(old_filename) and f.endswith('.bak')],
                reverse=True,
            )
            for old_bak in backups[5:]:
                try:
                    os.remove(os.path.join(backup_dir, old_bak))
                except OSError:
                    pass
        except Exception as e:
            logger.warning(f"[Firefly] 备份文章 {old_filename} 失败: {e}")

        metadata.updated = datetime.now().strftime("%Y-%m-%d")
        full_content = metadata.to_yaml() + "\n" + body

        if await self.blog_manager.write_post(old_filename, full_content):
            yield f"[OK] 文章《{metadata.title}》更新成功\n提示：需要重新构建部署才能生效"
        else:
            yield f"[ERROR] 更新文章《{metadata.title}》失败"

    @filter.llm_tool(name="search_blog_posts")
    @require_blog_manager
    async def search_posts(self, event, keyword: str):
        '''在 Firefly 博客中搜索文章。支持精确匹配和正则表达式。

        Args:
            keyword(string): 搜索关键词（支持正则表达式）
        '''
        results = await self._search_posts(keyword)

        if not results:
            yield f"[INFO] 未找到包含「{keyword}」的文章"
        else:
            yield self._format_post_list(results)

    @filter.llm_tool(name="list_blog_categories")
    @require_blog_manager
    async def list_categories(self, event):
        '''列出 Firefly 博客中所有文章使用过的分类。'''
        posts = await self.blog_manager.list_posts()
        categories = {}
        for post in posts:
            if post.category:
                categories[post.category] = categories.get(post.category, 0) + 1
        if not categories:
            yield "[INFO] 博客中没有分类"
        else:
            items = sorted(categories.items(), key=lambda x: -x[1])
            lines = [f"{cat}（{count} 篇）" for cat, count in items]
            yield "博客分类：\n" + "\n".join(lines)

    @filter.llm_tool(name="list_blog_tags")
    @require_blog_manager
    async def list_tags(self, event):
        '''列出 Firefly 博客中所有文章使用过的标签。'''
        posts = await self.blog_manager.list_posts()
        tags = {}
        for post in posts:
            for tag in post.tags:
                tags[tag] = tags.get(tag, 0) + 1
        if not tags:
            yield "[INFO] 博客中没有标签"
        else:
            items = sorted(tags.items(), key=lambda x: -x[1])
            lines = [f"{tag}（{count} 篇）" for tag, count in items]
            yield "博客标签：\n" + "\n".join(lines)

    async def _search_posts(self, keyword: str) -> list[PostInfo]:
        """按关键词搜索文章（标题/分类/标签/正文），支持精确匹配和模糊匹配"""
        posts = await self.blog_manager.list_posts()
        results = []
        keyword_lower = keyword.lower()

        # 尝试作为正则编译，失败则退化为普通文本匹配
        pattern = None
        try:
            pattern = re.compile(keyword, re.IGNORECASE)
        except re.error:
            pattern = None

        for post in posts:
            if (keyword_lower in post.title.lower()
                    or keyword_lower in post.category.lower()
                    or any(keyword_lower in t.lower() for t in post.tags)):
                results.append(post)
                continue
            # 正则匹配：标题/分类/标签/正文
            if pattern:
                try:
                    if (pattern.search(post.title)
                            or pattern.search(post.category)
                            or any(pattern.search(t) for t in post.tags)):
                        results.append(post)
                        continue
                except Exception:
                    pass
            # 正文内容匹配
            try:
                content = await self.blog_manager.read_post(post.filename)
                if content and keyword_lower in content.lower():
                    results.append(post)
                elif content and pattern and pattern.search(content):
                    results.append(post)
            except Exception:
                pass
        return results

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
        yield self._check_memory_status()

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
            # 远程模式下，检查远端博客仓库的 dist（remote_blog_root）
            remote_blog_root = self.build_manager.remote_blog_root
            rc, _, _ = await self.build_manager.remote_executor.run(f"test -d {shlex.quote(remote_blog_root)}/dist", timeout=5)
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
        deploy_mode = parse_deploy_mode(self.config.get("deploy_mode", "local_build"))
        
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
        submission_id = uuid.uuid4().hex[:12]
        submit_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        user_id = get_user_id(event)
        
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
            yield f"[INFO] 暂无{SUBMISSION_STATUS_TEXT.get(status, '')}的投稿"
            return

        stats = self._get_submission_stats()
        result = f"[INFO] 投稿列表（共 {stats['total']} 条 | 待审核 {stats['pending']} | 已批准 {stats['approved']} | 已拒绝 {stats['rejected']}）\n\n"
        for sub in submissions:
            result += f"{SUBMISSION_STATUS_EMOJI.get(sub.status, '❓')} {sub.title}\n"
            result += f"   - 投稿 ID: `{sub.id}`\n"
            result += f"   - 作者: {sub.author_name or '匿名'}\n"
            result += f"   - 提交时间: {sub.submit_time}\n"
            status_text = SUBMISSION_STATUS_TEXT.get(sub.status, sub.status)
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
        result += f"状态: {SUBMISSION_STATUS_TEXT.get(submission['status'], submission['status'])}\n"
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
            status_text = SUBMISSION_STATUS_TEXT.get(
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
            status_text = SUBMISSION_STATUS_TEXT.get(
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
            status_text = SUBMISSION_STATUS_TEXT.get(
                submission.get("status"), submission.get("status")
            )
            yield f"[ERROR] 投稿状态为「{status_text}」，只有待审核的投稿才能撤回"
            return

        # 验证投稿者身份
        user_id = get_user_id(event)
        sub_user_id = submission.get("user_id")
        if user_id and sub_user_id is not None:
            if user_id != str(sub_user_id):
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
        results = await self._search_posts(keyword)
        if not results:
            yield event.plain_result(f"[INFO] 未找到包含「{keyword}」的文章")
        else:
            yield event.plain_result(self._format_post_list(results))

    @filter.command("博客投稿", alias=["提交投稿", "投稿文章"], priority=5)
    @require_blog_manager
    async def cmd_submit_post(self, event, title: str = "", tags: str = "", category: str = ""):
        """提交文章投稿。

        Args:
            title(string): 文章标题，正文内容通过后续消息或分号分隔提供
            tags(string): 文章标签，多个用逗号分隔
            category(string): 文章分类

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

        # 获取用户信息
        author_name = (
            getattr(event, 'sender_name', None) or
            getattr(event, 'user_name', None) or
            getattr(event, 'nickname', None) or "匿名用户"
        )
        user_id = get_user_id(event)

        submission_id = uuid.uuid4().hex[:12]
        sub = {
            "id": submission_id,
            "title": title.strip(),
            "content": content.strip(),
            "author_name": str(author_name),
            "user_id": user_id,
            "submit_time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "status": "pending",
            "reject_reason": "",
            "tags": tags.strip(),
            "category": category.strip(),
            "description": "",
        }
        self._submissions_cache[submission_id] = sub
        self._save_submissions()

        tag_info = f"\n   - 标签: {tags.strip()}" if tags.strip() else ""
        cat_info = f"\n   - 分类: {category.strip()}" if category.strip() else ""
        yield event.plain_result(
            f"[OK] 投稿已提交！\n"
            f"   - 投稿 ID: `{submission_id}`\n"
            f"   - 标题: {title.strip()}\n"
            f"   - 状态: 待审核\n{tag_info}{cat_info}"
            f"   - 使用 `/我的投稿` 查看您的投稿状态\n"
            f"   - 管理员将在审核后决定是否发布"
        )

    @filter.command("我的投稿", alias=["我的提交", "投稿状态"], priority=5)
    async def cmd_my_submissions(self, event):
        """查看自己的投稿记录和状态"""
        user_id = get_user_id(event)

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

        lines = ["📋 **我的投稿记录**", ""]
        for sub in my_subs:
            emoji = SUBMISSION_STATUS_EMOJI.get(sub.status, "❓")
            text = SUBMISSION_STATUS_TEXT.get(sub.status, sub.status)
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
            status_str = SUBMISSION_STATUS_BADGE.get(sub["status"], "[UNKNOWN]")
            author = sub["author_name"] or "匿名"
            result += f"{status_str} {sub['title']} - {author} - {sub['submit_time']}\n"
        yield event.plain_result(result)

    @filter.command("内存状态", alias=["检查内存"], priority=5)
    @require_admin
    async def cmd_memory_status(self, event):
        """检查当前内存状态（公开命令）"""
        yield event.plain_result(self._check_memory_status())

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
            f"🔥 **Firefly 博客管理插件 v{PLUGIN_VERSION}**",
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
