"""模块说明见 main.py 顶部文档。"""

from __future__ import annotations

import functools
import os
from typing import Optional

from astrbot.api import logger

from .models import DeployMode



# ============================================================================
# 部署模式解析
# ============================================================================

def parse_deploy_mode(value: Optional[str]) -> DeployMode:
    """安全解析部署模式配置，无效值回退到默认模式（local_build）

    Args:
        value: 配置中的 deploy_mode 值

    Returns:
        有效的 DeployMode，配置无效时使用默认值并记录警告
    """
    if value not in [m.value for m in DeployMode]:
        logger.warning(f"[Firefly] 无效的部署模式: {value}，使用默认值 local_build")
        return DeployMode.LOCAL_BUILD
    return DeployMode(value)


# ============================================================================
# 用户身份获取
# ============================================================================

def get_user_id(event) -> str:
    """从事件对象中提取用户 ID（兼容多种事件字段）

    投稿相关功能依赖用户 ID 做身份区分，不同平台适配器暴露的
    字段名可能不同，这里做兼容性提取。
    """
    user_id = (
        getattr(event, 'user_id', None) or
        getattr(event, 'sender_id', None) or
        getattr(event, 'from_id', None) or
        getattr(event, 'user_id_holder', None)
    )
    return str(user_id) if user_id is not None else ""


# ============================================================================
# Firefly 博客项目检测
# ============================================================================

def is_firefly_blog_local(path: str) -> bool:
    """检查本地路径是否为 Firefly 博客项目

    通过 Firefly 博客的特征文件判断（至少满足两个条件）：
    package.json / src/content/posts / astro.config.mjs
    """
    if not os.path.isdir(path):
        return False

    matches = 0
    if os.path.isfile(os.path.join(path, "package.json")):
        matches += 1
    if os.path.isdir(os.path.join(path, "src", "content", "posts")):
        matches += 1
    if os.path.isfile(os.path.join(path, "astro.config.mjs")):
        matches += 1

    return matches >= 2


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
