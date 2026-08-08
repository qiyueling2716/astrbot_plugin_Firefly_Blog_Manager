"""Firefly 博客管理插件 WebUI 模块。

实现 AstrBot Dashboard 插件 Page 的后端 Web API：
- 博客状态查看（部署模式、博客目录检测、构建状态、内存、文章数）
- src 目录文件管理（列表、读取、编辑保存、新建、删除、上传）
- 博客目录自定义（未检测到博客时手动指定目录）
- 友链（friendsConfig.ts）管理
- 站点配置（src/config 下纯对象配置文件）结构化编辑

页面文件位于插件目录 pages/manager/，通过 AstrBot 框架的
context.register_web_api() 注册后端接口，由 Dashboard 以受限 iframe
方式加载并通过 window.AstrBotPluginPage bridge 通信。
"""

from __future__ import annotations

import asyncio
import functools
import json
import os
import posixpath
import random
import re
import stat as stat_module
from typing import Optional

from astrbot.api import logger
from astrbot.api.web import error_response, json_response, request

from .common import is_firefly_blog_local, parse_deploy_mode
from .constants import (
    DEFAULT_BLOG_ROOT,
    EXTERNAL_CONFIG_FILES,
    PLUGIN_NAME,
    PLUGIN_VERSION,
)
from .filesystem import LocalFileSystem, RemoteFileSystem
from .models import DeployMode

MAX_FILE_READ_BYTES = 2 * 1024 * 1024  # 网页预览文件大小上限（2MB）

IMAGE_PREVIEW_EXTS = (
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".ico", ".bmp",
)
IMAGE_PREVIEW_MAX_BYTES = 8 * 1024 * 1024  # 图片预览上限（8MB）

WEB_ROUTES = [
    ("status", "GET", "博客状态概览"),
    ("config", "GET", "获取当前配置"),
    ("config", "POST", "保存配置（自定义博客目录）"),
    ("files", "GET", "列出 src 目录文件"),
    ("file", "GET", "读取文件内容"),
    ("file", "POST", "写入文件内容"),
    ("file/delete", "POST", "删除文件或空目录"),
    ("file/upload/<path:dir>", "POST", "上传文件到目录"),
    ("links", "GET", "获取友链列表"),
    ("links", "POST", "保存友链列表"),
    ("external", "GET", "列出对外展示配置文件及可编辑字段"),
    ("external-items", "GET", "读取对外展示字段的条目列表"),
    ("external", "POST", "保存对外展示字段的条目列表"),
    ("config-files", "GET", "列出 src/config 配置文件"),
    ("config-file", "GET", "读取配置文件（结构化）"),
    ("config-file", "POST", "保存配置文件（结构化）"),
    ("wallpaper", "GET", "随机壁纸（桌面端/移动端）"),
]


# ============================================================================
# TypeScript 解析工具（friendsConfig.ts）
# ============================================================================

def _strip_ts_comments(text: str) -> str:
    """移除 TS 代码中的 // 和 /* */ 注释（保留字符串字面量内的内容）"""
    out = []
    i, n = 0, len(text)
    in_str = False
    while i < n:
        ch = text[i]
        if in_str:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if ch == '"':
                in_str = False
            i += 1
            continue
        if ch == '"':
            in_str = True
            out.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < n:
            nxt = text[i + 1]
            if nxt == "/":
                while i < n and text[i] != "\n":
                    i += 1
                continue
            if nxt == "*":
                i += 2
                while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                    i += 1
                i = min(i + 2, n)
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def _find_matching_bracket(text: str, open_idx: int, open_ch: str, close_ch: str) -> int:
    """从 open_idx 开始查找与 open_ch 匹配的 close_ch 位置（字符串与注释感知）"""
    depth = 0
    i, n = open_idx, len(text)
    while i < n:
        ch = text[i]
        if ch in "\"'":
            quote = ch
            i += 1
            while i < n:
                if text[i] == "\\":
                    i += 2
                    continue
                if text[i] == quote:
                    break
                i += 1
            i += 1
            continue
        if ch == "/" and i + 1 < n:
            if text[i + 1] == "/":
                while i < n and text[i] != "\n":
                    i += 1
                continue
            if text[i + 1] == "*":
                i += 2
                while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                    i += 1
                i = min(i + 2, n)
                continue
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _split_top_level(text: str) -> list[str]:
    """按顶层逗号拆分（字符串与括号感知），返回去除首尾空白的分段"""
    parts = []
    start = 0
    depth = 0
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch in "\"'":
            quote = ch
            i += 1
            while i < n:
                if text[i] == "\\":
                    i += 2
                    continue
                if text[i] == quote:
                    break
                i += 1
            i += 1
            continue
        if ch in "{[(":
            depth += 1
        elif ch in "}])":
            depth = max(0, depth - 1)
        elif ch == "," and depth == 0:
            parts.append(text[start:i].strip())
            start = i + 1
        i += 1
    parts.append(text[start:].strip())
    return [p for p in parts if p]


def _decode_js_value(raw: str):
    """解析 JS 字面量值：字符串、数字、布尔、字符串数组、null

    无法识别的值原样返回字符串（重建时逐字写回）。
    """
    value = raw.strip()
    if not value:
        return ""
    if value.startswith('"'):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return value
    if value.startswith("["):
        inner = value[1:].rsplit("]", 1)[0] if value.endswith("]") else value[1:]
        items = []
        for part in _split_top_level(inner):
            item = part.strip()
            if item.startswith('"'):
                try:
                    items.append(json.loads(item))
                except (ValueError, TypeError):
                    items.append(item)
            else:
                items.append(item)
        return items
    if value == "true":
        return True
    if value == "false":
        return False
    if value == "null":
        return None
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def parse_friend_links(content: str) -> tuple[list[dict], Optional[str]]:
    """解析 friendsConfig.ts 中的 friendsConfig 数组

    Returns:
        (友链列表, 错误信息)；解析成功时错误信息为 None
    """
    text = _strip_ts_comments(content)
    anchor = text.find("export const friendsConfig")
    if anchor == -1:
        anchor = text.find("friendsConfig")
    if anchor == -1:
        return [], "未找到 friendsConfig 定义"
    eq_idx = text.find("=", anchor)
    if eq_idx == -1:
        return [], "未找到 friendsConfig 赋值"
    open_idx = text.find("[", eq_idx)
    if open_idx == -1:
        return [], "未找到 friendsConfig 数组"
    close_idx = _find_matching_bracket(text, open_idx, "[", "]")
    if close_idx == -1:
        return [], "friendsConfig 数组未闭合"

    array_body = text[open_idx + 1 : close_idx]
    links = []
    for chunk in _split_top_level(array_body):
        chunk = chunk.strip()
        if not chunk:
            continue
        if not chunk.startswith("{"):
            continue
        link = {}
        body = chunk[1:].rsplit("}", 1)[0] if chunk.endswith("}") else chunk[1:]
        for pair in _split_top_level(body):
            colon = pair.find(":")
            if colon == -1:
                continue
            key = pair[:colon].strip().strip('"').strip("'")
            raw_value = pair[colon + 1 :]
            link[key] = _decode_js_value(raw_value)
        links.append(link)
    return links, None


def _js_repr(value) -> str:
    """将解析后的值序列化为 JS 字面量"""
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_js_repr(v) for v in value) + "]"
    if isinstance(value, dict) and "__raw__" in value:
        return value["__raw__"]
    return json.dumps(str(value), ensure_ascii=False)


_KNOWN_LINK_KEYS = ["title", "imgurl", "desc", "siteurl", "tags", "weight", "enabled"]


def rebuild_friends_config(content: str, links: list[dict]) -> str:
    """根据友链列表重建 friendsConfig 数组（保留文件其余部分）"""
    text = _strip_ts_comments(content)
    anchor = text.find("export const friendsConfig")
    if anchor == -1:
        anchor = text.find("friendsConfig")
    if anchor == -1:
        raise ValueError("未找到 friendsConfig 定义")
    # 在注释剥离前定位同一锚点在原文件中的位置，保证切片偏移正确
    raw_anchor = content.find("export const friendsConfig")
    if raw_anchor == -1:
        raw_anchor = content.find("friendsConfig")
    eq_idx = content.find("=", raw_anchor)
    if eq_idx == -1:
        raise ValueError("未找到 friendsConfig 赋值")
    open_idx = content.find("[", eq_idx)
    if open_idx == -1:
        raise ValueError("未找到 friendsConfig 数组")
    close_idx = _find_matching_bracket(content, open_idx, "[", "]")
    if close_idx == -1:
        raise ValueError("friendsConfig 数组未闭合")

    lines = ["["]
    for link in links:
        lines.append("\t{")
        for key in _KNOWN_LINK_KEYS:
            if key in link:
                lines.append(f"\t\t{key}: {_js_repr(link[key])},")
        for key, value in link.items():
            if key not in _KNOWN_LINK_KEYS:
                lines.append(f"\t\t{key}: {_js_repr(value)},")
        lines.append("\t},")
    lines.append("]")
    block = "\n".join(lines)

    return content[:open_idx] + block + content[close_idx + 1 :]


# ============================================================================
# 通用 TS 配置对象解析 / 重建（siteConfig.ts 等纯对象配置文件）
# ============================================================================

def _decode_ts_single_quote(s: str) -> Optional[str]:
    """解码单引号字符串（含常见转义），无法安全解码时返回 None"""
    if len(s) < 2 or s[-1] != "'":
        return None
    inner = s[1:-1]
    out = []
    i, n = 0, len(inner)
    escapes = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", "'": "'", '"': '"'}
    while i < n:
        ch = inner[i]
        if ch == "\\" and i + 1 < n:
            nxt = inner[i + 1]
            out.append(escapes.get(nxt, "\\" + nxt))
            i += 2
            continue
        if ch == "'":
            return None
        out.append(ch)
        i += 1
    return "".join(out)


def _parse_ts_value(raw: str):
    """递归解析 TS 值（字符串/数字/布尔/null/undefined/对象/数组）

    无法识别的值（枚举引用、函数调用、模板字符串等）以
    {"__raw__": 原文} 标记，重建时逐字写回。
    """
    value = raw.strip()
    if not value:
        return ""
    if value.startswith('"'):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return {"__raw__": value}
    if value.startswith("'"):
        decoded = _decode_ts_single_quote(value)
        if decoded is not None:
            return decoded
        return {"__raw__": value}
    if value.startswith("{"):
        close_idx = _find_matching_bracket(value, 0, "{", "}")
        if close_idx == -1:
            return {"__raw__": value}
        return _parse_ts_object_body(value[1:close_idx])
    if value.startswith("["):
        close_idx = _find_matching_bracket(value, 0, "[", "]")
        if close_idx == -1:
            return {"__raw__": value}
        items = []
        for part in _split_top_level(value[1:close_idx]):
            if part.strip():
                items.append(_parse_ts_value(part))
        return items
    if value == "true":
        return True
    if value == "false":
        return False
    if value in ("null", "undefined"):
        return None
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return {"__raw__": value}


def _parse_ts_object_body(body: str) -> dict:
    """解析对象字面量内部（不含外层大括号）为 dict"""
    data = {}
    for pair in _split_top_level(body):
        colon = pair.find(":")
        if colon == -1:
            continue
        key = pair[:colon].strip().strip('"').strip("'")
        if not key:
            continue
        data[key] = _parse_ts_value(pair[colon + 1 :])
    return data


def parse_ts_config_objects(content: str) -> list[dict]:
    """解析文件中所有纯字面量导出（对象或数组）

    每个结果: {"name": 导出名, "kind": "object"|"array", "data": 解析后的值}。
    仅支持 `export const name[: Type] = { ... }` / `= [ ... ]` 形式；
    含函数调用/动态逻辑（如 navBarConfig.ts）或非字面量导出的被跳过。
    """
    text = _strip_ts_comments(content)
    results = []
    for m in re.finditer(r"export\s+const\s+([A-Za-z_$][\w$]*)", text):
        name = m.group(1)
        eq_idx = text.find("=", m.end())
        if eq_idx == -1:
            continue
        rest = text[eq_idx + 1 :].lstrip()
        if rest.startswith("["):
            data = _parse_ts_value(rest)
            if isinstance(data, list):
                results.append({"name": name, "kind": "array", "data": data})
        elif rest.startswith("{"):
            open_idx = text.find("{", eq_idx)
            if open_idx == -1:
                continue
            between_end = text[eq_idx + 1 : open_idx].strip()
            if between_end and not between_end.startswith(":"):
                continue
            close_idx = _find_matching_bracket(text, open_idx, "{", "}")
            if close_idx == -1:
                continue
            data = _parse_ts_object_body(text[open_idx + 1 : close_idx])
            if data is not None:
                results.append({"name": name, "kind": "object", "data": data})
    return results


def parse_ts_config_object(content: str) -> Optional[dict]:
    """解析文件中第一个纯对象导出的 TS 配置

    支持 `export const name: Type = { ... }`（含类型标注）与 `export const name = {...}`
    形式的纯对象字面量；多个导出时返回第一个可解析为对象字面量的。
    值含函数调用/动态逻辑（如 navBarConfig.ts）或非对象导出时返回 None。

    Returns:
        {"name": 导出名, "data": 解析后的对象} 或 None
    """
    for exp in parse_ts_config_objects(content):
        if exp["kind"] == "object":
            return {"name": exp["name"], "data": exp["data"]}
    return None


def _rebuild_ts_value(value, level: int) -> str:
    """将解析后的配置值序列化为 TS 字面量（tab 缩进）"""
    indent = "\t" * level
    child_indent = "\t" * (level + 1)
    if isinstance(value, dict):
        if "__raw__" in value:
            return str(value["__raw__"])
        if not value:
            return "{}"
        lines = ["{"]
        for key, val in value.items():
            lines.append(f"{child_indent}{key}: {_rebuild_ts_value(val, level + 1)},")
        lines.append(indent + "}")
        return "\n".join(lines)
    if isinstance(value, list):
        if not value:
            return "[]"
        if any(isinstance(v, (dict, list)) for v in value):
            lines = ["["]
            for item in value:
                lines.append(child_indent + _rebuild_ts_value(item, level + 1) + ",")
            lines.append(indent + "]")
            return "\n".join(lines)
        return "[" + ", ".join(_js_repr(v) for v in value) + "]"
    return _js_repr(value)


def _find_char_skip_comments(text: str, start: int, target: str) -> int:
    """从 start 起查找第一个 target 字符（跳过字符串与注释）"""
    i, n = start, len(text)
    in_str = False
    while i < n:
        ch = text[i]
        if in_str:
            if ch == "\\":
                i += 2
                continue
            if ch == '"':
                in_str = False
            i += 1
            continue
        if ch == '"':
            in_str = True
            i += 1
            continue
        if ch == "/" and i + 1 < n:
            if text[i + 1] == "/":
                while i < n and text[i] != "\n":
                    i += 1
                continue
            if text[i + 1] == "*":
                i += 2
                while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                    i += 1
                i = min(i + 2, n)
                continue
        if ch == target:
            return i
        i += 1
    return -1


def rebuild_ts_config(content: str, export_name: str, data: dict) -> str:
    """按解析后的对象重建配置文件中的对应导出块（保留文件其余部分）"""
    raw_m = re.search(r"export\s+const\s+" + re.escape(export_name) + r"\b", content)
    if not raw_m:
        raise ValueError(f"未找到 {export_name} 定义")
    eq_idx = _find_char_skip_comments(content, raw_m.end(), "=")
    if eq_idx == -1:
        raise ValueError(f"未找到 {export_name} 赋值")
    open_idx = _find_char_skip_comments(content, eq_idx + 1, "{")
    if open_idx == -1:
        raise ValueError(f"未找到 {export_name} 对象")
    between = content[eq_idx + 1 : open_idx].strip()
    if between and not between.startswith(":"):
        raise ValueError(f"{export_name} 不是纯对象配置")
    close_idx = _find_matching_bracket(content, open_idx, "{", "}")
    if close_idx == -1:
        raise ValueError(f"{export_name} 对象未闭合")
    block = _rebuild_ts_value(data, 0)
    return content[:open_idx] + block + content[close_idx + 1 :]


def rebuild_ts_array_export(content: str, export_name: str, items: list) -> str:
    """替换 `export const name[: Type] = [...]` 的数组字面量（保留文件其余部分）"""
    raw_m = re.search(r"export\s+const\s+" + re.escape(export_name) + r"\b", content)
    if not raw_m:
        raise ValueError(f"未找到 {export_name} 定义")
    eq_idx = _find_char_skip_comments(content, raw_m.end(), "=")
    if eq_idx == -1:
        raise ValueError(f"未找到 {export_name} 赋值")
    open_idx = _find_char_skip_comments(content, eq_idx + 1, "[")
    if open_idx == -1:
        raise ValueError(f"未找到 {export_name} 数组")
    between = content[eq_idx + 1 : open_idx].strip()
    if between and not between.startswith(":"):
        raise ValueError(f"{export_name} 不是纯数组导出")
    close_idx = _find_matching_bracket(content, open_idx, "[", "]")
    if close_idx == -1:
        raise ValueError(f"{export_name} 数组未闭合")
    block = _rebuild_ts_array(items, 0)
    return content[:open_idx] + block + content[close_idx + 1 :]


def _rebuild_ts_array(items: list, level: int = 0) -> str:
    """将数组序列化为 TS 数组字面量（tab 缩进）"""
    indent = "\t" * level
    child_indent = "\t" * (level + 1)
    if not items:
        return "[]"
    lines = ["["]
    for item in items:
        lines.append(child_indent + _rebuild_ts_value(item, level + 1) + ",")
    lines.append(indent + "]")
    return "\n".join(lines)


# ============================================================================
# WebUI 后端
# ============================================================================

def _web_safe(view):
    """Web handler 异常兜底：记录完整堆栈，并把真实错误返回给页面"""

    @functools.wraps(view)
    async def wrapped(*args, **kwargs):
        try:
            return await view(*args, **kwargs)
        except Exception as e:
            logger.exception(f"[Firefly] Web API 处理异常 ({view.__name__}): {e}")
            return error_response(f"后端处理异常: {type(e).__name__}: {e}")

    return wrapped


class WebUIMixin:
    """博客管理 WebUI 后端接口（由 FireflyBlogManager 混入）"""

    def register_web_apis(self) -> None:
        """注册所有 Dashboard 页面 API"""
        for endpoint, method, desc in WEB_ROUTES:
            route = f"/{PLUGIN_NAME}/{endpoint}"
            view = _web_safe(self._web_route_handlers[endpoint])
            self.context.register_web_api(route, view, [method], desc)
            logger.info(f"[Firefly] 已注册 Web API: {method} {route}")

    # ------------------------------------------------------------------
    # 路由分发
    # ------------------------------------------------------------------

    @property
    def _web_route_handlers(self) -> dict:
        return {
            "status": self._web_status,
            "config": self._web_config,
            "files": self._web_files_list,
            "file": self._web_file,
            "file/delete": self._web_file_delete,
            "file/upload/<path:dir>": self._web_file_upload,
            "links": self._web_links,
            "external": self._web_external,
            "external-items": self._web_external_items,
            "config-files": self._web_config_files,
            "config-file": self._web_config_file,
            "wallpaper": self._web_wallpaper,
        }

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------

    def _resolve_src_path(self, rel_path: str) -> str:
        """将网页传入的相对路径安全解析到 src 根目录内

        Raises:
            ValueError: 路径非法（穿越、绝对路径等）
        """
        rel = (rel_path or "").strip().replace("\\", "/").lstrip("/")
        parts = [p for p in rel.split("/") if p not in ("", ".")]
        if any(p == ".." for p in parts):
            raise ValueError("路径不允许包含 ..")
        src_root = self.src_root
        if isinstance(self.fs, LocalFileSystem):
            full = os.path.normpath(os.path.join(src_root, *parts))
            if os.path.commonpath([full, os.path.normpath(src_root)]) != os.path.normpath(src_root):
                raise ValueError("路径越界")
            return full
        full = posixpath.normpath(posixpath.join(src_root, *parts))
        norm_root = posixpath.normpath(src_root)
        if full != norm_root and not full.startswith(norm_root.rstrip("/") + "/"):
            raise ValueError("路径越界")
        return full

    def _effective_blog_root(self) -> tuple[str, str, bool]:
        """返回 (博客根目录, 来源, 是否已构建)

        来源: configured / auto / remote / not-found
        """
        deploy_mode = parse_deploy_mode(self.config.get("deploy_mode", "local_build"))
        if deploy_mode in (DeployMode.LOCAL_BUILD, DeployMode.LOCAL_ONLY):
            configured = str(self.config.get("local_blog_root", "") or "").strip()
            if configured and os.path.isdir(configured):
                return configured, "configured", self._is_blog_built(configured)
            blog_root, is_built, auto = self._find_local_blog_root()
            if auto:
                return blog_root, "auto", is_built
            return blog_root, "not-found", False
        blog_root = str(self.config.get("remote_blog_root", "") or DEFAULT_BLOG_ROOT)
        return blog_root, "remote", False

    async def _list_dir_entries(self, full_path: str) -> list[dict]:
        """列出目录条目 (name/type/size/mtime)"""
        if isinstance(self.fs, LocalFileSystem):
            def _scan():
                entries = []
                try:
                    with os.scandir(full_path) as it:
                        for entry in it:
                            try:
                                st = entry.stat()
                                entries.append({
                                    "name": entry.name,
                                    "type": "dir" if entry.is_dir() else "file",
                                    "size": st.st_size,
                                    "mtime": int(st.st_mtime),
                                })
                            except OSError:
                                entries.append({"name": entry.name, "type": "file", "size": 0, "mtime": 0})
                except OSError:
                    return []
                return sorted(entries, key=lambda e: (e["type"] != "dir", e["name"].lower()))

            return await asyncio.to_thread(_scan)

        if isinstance(self.fs, RemoteFileSystem):
            try:
                sftp = await self.fs.executor.get_sftp()
                names = await sftp.listdir(full_path)
            except Exception as e:
                logger.error(f"[Firefly] 远程目录读取失败 {full_path}: {e}")
                return []

            async def _stat_one(name: str) -> dict:
                entry_path = posixpath.join(full_path, name)
                try:
                    attrs = await sftp.stat(entry_path)
                    perms = getattr(attrs, "permissions", None)
                    is_dir = bool(
                        perms is not None
                        and (perms & stat_module.S_IFMT(perms) == stat_module.S_IFDIR)
                    )
                    if perms is not None and (perms & 0o170000) == 0:
                        try:
                            await sftp.listdir(entry_path)
                            is_dir = True
                        except Exception:
                            is_dir = False
                    return {
                        "name": name,
                        "type": "dir" if is_dir else "file",
                        "size": int(getattr(attrs, "size", 0) or 0),
                        "mtime": int(getattr(attrs, "mtime", 0) or 0),
                    }
                except Exception:
                    return {"name": name, "type": "file", "size": 0, "mtime": 0}

            entries = await asyncio.gather(*[_stat_one(n) for n in names])
            return sorted(entries, key=lambda e: (e["type"] != "dir", e["name"].lower()))

        return []

    # ------------------------------------------------------------------
    # 状态 / 配置
    # ------------------------------------------------------------------

    async def _web_status(self):
        deploy_mode = parse_deploy_mode(self.config.get("deploy_mode", "local_build"))
        blog_root, source, is_built = self._effective_blog_root()
        src_root = getattr(self, "src_root", "")
        firefly_detected = (
            bool(src_root)
            and os.path.isdir(src_root)
            and is_firefly_blog_local(os.path.dirname(src_root))
        )
        posts_count = 0
        try:
            posts = await self.blog_manager.list_posts()
            posts_count = len(posts)
        except Exception as e:
            logger.warning(f"[Firefly] 网页获取文章数失败: {e}")

        memory = self._check_memory_status()

        return json_response({
            "status": "ok",
            "data": {
                "version": PLUGIN_VERSION,
                "deploy_mode": deploy_mode.value,
                "blog_root": blog_root,
                "blog_root_source": source,
                "blog_root_exists": bool(src_root) and os.path.isdir(src_root),
                "firefly_detected": firefly_detected,
                "is_built": is_built,
                "posts_count": posts_count,
                "src_root": src_root,
                "memory": memory,
                "remote_mode": deploy_mode == DeployMode.REMOTE_BUILD,
                "config": {
                    "local_blog_root": str(self.config.get("local_blog_root", "") or ""),
                    "remote_blog_root": str(self.config.get("remote_blog_root", "") or ""),
                    "web_root": str(self.config.get("web_root", "") or ""),
                    "remote_web_root": str(self.config.get("remote_web_root", "") or ""),
                    "build_memory_threshold": self.config.get("build_memory_threshold", 1536),
                    "allow_build_concurrent": self.config.get("allow_build_concurrent", False),
                },
            },
        })

    async def _web_config(self):
        if request.method == "GET":
            return await self._web_status()

        data = await request.json(default={})
        if not isinstance(data, dict):
            return error_response("无效的请求数据")

        deploy_mode = parse_deploy_mode(self.config.get("deploy_mode", "local_build"))
        if deploy_mode in (DeployMode.LOCAL_BUILD, DeployMode.LOCAL_ONLY):
            custom_root = str(data.get("local_blog_root", "") or "").strip()
            if data.get("redetect"):
                self.config["local_blog_root"] = ""
            elif custom_root:
                if not os.path.isdir(custom_root):
                    return error_response(f"目录不存在: {custom_root}")
                self.config["local_blog_root"] = custom_root
            try:
                await self.config.save_config_async()
            except Exception as e:
                logger.error(f"[Firefly] 保存配置失败: {e}")
                return error_response(f"保存配置失败: {e}")
            self._init_components()
        return await self._web_status()

    # ------------------------------------------------------------------
    # 文件管理
    # ------------------------------------------------------------------

    async def _web_files_list(self):
        rel_path = request.query.get("path", "")
        try:
            full = self._resolve_src_path(rel_path)
        except ValueError as e:
            return error_response(f"非法路径: {e}")

        if not await self.fs.exists(full):
            return error_response(f"路径不存在: {rel_path or 'src'}")

        if isinstance(self.fs, LocalFileSystem) and not os.path.isdir(full):
            return error_response(f"不是目录: {rel_path or 'src'}")

        entries = await self._list_dir_entries(full)
        return json_response({"status": "ok", "data": {"path": rel_path or "", "entries": entries}})

    async def _web_file(self):
        if request.method == "GET":
            return await self._read_file_handler()
        return await self._write_file_handler()

    async def _read_file_handler(self):
        rel_path = request.query.get("path", "")
        try:
            full = self._resolve_src_path(rel_path)
        except ValueError as e:
            return error_response(f"非法路径: {e}")

        lower = rel_path.lower()
        if lower.endswith(IMAGE_PREVIEW_EXTS):
            try:
                size = os.path.getsize(full)
            except OSError:
                size = -1
            if size > IMAGE_PREVIEW_MAX_BYTES:
                return error_response(f"图片过大（{size} 字节），无法预览")
            try:
                with open(full, "rb") as f:
                    raw = f.read()
            except OSError:
                return error_response(f"无法读取图片: {rel_path}")
            import base64
            mime = "image/svg+xml" if lower.endswith(".svg") else "image/" + lower.rsplit(".", 1)[-1]
            data_url = f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")
            return json_response({
                "status": "ok",
                "data": {"path": rel_path or "", "content": data_url, "binary": True, "image": True},
            })

        if isinstance(self.fs, LocalFileSystem):
            try:
                size = os.path.getsize(full)
            except OSError:
                size = -1
            if size > MAX_FILE_READ_BYTES:
                return error_response(f"文件过大（{size} 字节），超过网页预览上限 {MAX_FILE_READ_BYTES} 字节")

        content = await self.fs.read_file(full)
        if content is None:
            return error_response(f"无法读取文件: {rel_path}")
        return json_response({
            "status": "ok",
            "data": {"path": rel_path or "", "content": content, "binary": False},
        })

    async def _write_file_handler(self):
        data = await request.json(default={})
        rel_path = str(data.get("path") or "")
        content = data.get("content")
        if not isinstance(content, str):
            return error_response("缺少文件内容")
        try:
            full = self._resolve_src_path(rel_path)
        except ValueError as e:
            return error_response(f"非法路径: {e}")
        if full.rstrip("/").endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".ico", ".bmp", ".woff", ".woff2", ".ttf")):
            return error_response("二进制资源请通过上传功能更新")
        ok = await self.fs.write_file(full, content)
        if not ok:
            return error_response(f"写入失败: {rel_path}")
        return json_response({"status": "ok", "data": {"path": rel_path or ""}})

    async def _web_file_delete(self):
        data = await request.json(default={})
        rel_path = str(data.get("path") or "")
        if not rel_path:
            return error_response("缺少路径")
        try:
            full = self._resolve_src_path(rel_path)
        except ValueError as e:
            return error_response(f"非法路径: {e}")
        if full == self.src_root:
            return error_response("不允许删除 src 根目录")
        ok = await self.fs.delete_file(full)
        if not ok:
            return error_response(f"删除失败: {rel_path}")
        return json_response({"status": "ok", "data": {"path": rel_path}})

    async def _web_file_upload(self, dir: str):
        files = await request.files()
        upload_file = files.get("file")
        if upload_file is None:
            return error_response("未收到文件（字段名应为 file）")

        raw_name = (upload_file.filename or "upload.bin").replace("\\", "/")
        filename = os.path.basename(raw_name) or "upload.bin"
        try:
            full_dir = self._resolve_src_path(dir or "")
            full_target = self._resolve_src_path(posixpath.join(dir or "", filename))
        except ValueError as e:
            return error_response(f"非法路径: {e}")

        try:
            if isinstance(self.fs, LocalFileSystem):
                os.makedirs(full_dir, exist_ok=True)
                await upload_file.save(full_target)
            elif isinstance(self.fs, RemoteFileSystem):
                sftp = await self.fs.executor.get_sftp()
                await self.fs._mkdir_recursive(sftp, full_dir)
                async with sftp.open(full_target, "wb") as f:
                    while True:
                        chunk = await upload_file.read(1024 * 1024)
                        if not chunk:
                            break
                        await f.write(chunk)
            else:
                return error_response("当前文件系统不支持上传")
        except Exception as e:
            logger.error(f"[Firefly] 上传失败: {e}")
            return error_response(f"上传失败: {e}")

        return json_response({
            "status": "ok",
            "data": {"path": posixpath.join(dir or "", filename)},
        })

    # ------------------------------------------------------------------
    # 友链管理
    # ------------------------------------------------------------------

    async def _links_config_path(self) -> str:
        """返回 friendsConfig.ts 完整路径（本地或远端 src 根下）"""
        return self._resolve_src_path("config/friendsConfig.ts")

    async def _web_links(self):
        try:
            config_path = await self._links_config_path()
        except ValueError as e:
            return error_response(f"非法路径: {e}")

        if request.method == "GET":
            content = await self.fs.read_file(config_path)
            if content is None:
                return error_response("未找到 src/config/friendsConfig.ts")
            links, err = parse_friend_links(content)
            if err:
                return error_response(f"解析 friendsConfig.ts 失败: {err}")
            return json_response({"status": "ok", "data": {"links": links}})

        data = await request.json(default={})
        links = data.get("links")
        if not isinstance(links, list):
            return error_response("无效的友链数据")
        for link in links:
            if not isinstance(link, dict):
                return error_response("无效的友链数据")

        content = await self.fs.read_file(config_path)
        if content is None:
            return error_response("未找到 src/config/friendsConfig.ts")
        try:
            rebuilt = rebuild_friends_config(content, links)
        except ValueError as e:
            return error_response(f"重建 friendsConfig.ts 失败: {e}")

        ok = await self.fs.write_file(config_path, rebuilt)
        if not ok:
            return error_response("写入 friendsConfig.ts 失败")
        return json_response({"status": "ok", "data": {"links": links}})

    # ------------------------------------------------------------------
    # 对外展示管理（friendsConfig.ts / socialConfig.ts / footerConfig.ts 等）
    # ------------------------------------------------------------------

    def _external_config_names(self) -> list[str]:
        """对外展示配置文件名单（默认 + 插件配置追加，大小写不敏感去重）"""
        merged = list(EXTERNAL_CONFIG_FILES)
        extra = self.config.get("external_config_files", [])
        if isinstance(extra, list):
            for n in extra:
                if isinstance(n, str) and n and not any(
                    n.lower() == m.lower() for m in merged
                ):
                    merged.append(n)
        return merged

    def _is_external_config(self, name: str) -> bool:
        return any(name.lower() == m.lower() for m in self._external_config_names())

    async def _web_external(self):
        if request.method == "GET":
            return await self._web_external_list()
        return await self._web_external_save()

    async def _web_external_list(self):
        files = []
        for name in self._external_config_names():
            full, err = self._resolve_config_file(name)
            if err or not await self.fs.exists(full):
                continue
            content = await self.fs.read_file(full)
            if content is None:
                continue
            targets = []
            for exp in parse_ts_config_objects(content):
                if exp["kind"] == "array":
                    targets.append(
                        {
                            "kind": "array",
                            "name": exp["name"],
                            "field": exp["name"],
                            "count": len(exp["data"]),
                        }
                    )
                else:
                    for k, v in exp["data"].items():
                        if isinstance(v, list):
                            targets.append(
                                {
                                    "kind": "object",
                                    "name": exp["name"],
                                    "field": k,
                                    "count": len(v),
                                }
                            )
            if targets:
                files.append({"name": name, "targets": targets})
        return json_response({"status": "ok", "data": {"files": files}})

    def _find_external_target(self, content: str, kind: str, name: str, field: str):
        """在文件中定位对外展示编辑目标，返回数组值或 None"""
        for exp in parse_ts_config_objects(content):
            if kind == "array" and exp["kind"] == "array" and exp["name"] == name:
                return exp["data"]
            if (
                kind == "object"
                and exp["kind"] == "object"
                and exp["name"] == name
                and isinstance(exp["data"].get(field), list)
            ):
                return exp["data"][field]
        return None

    async def _web_external_items(self):
        name = request.query.get("file", "")
        kind = request.query.get("kind", "")
        exp_name = request.query.get("name", "")
        field = request.query.get("field", "")
        full, err = self._resolve_config_file(name)
        if err:
            return error_response(err)
        content = await self.fs.read_file(full)
        if content is None:
            return error_response(f"无法读取 {name}")
        items = self._find_external_target(content, kind, exp_name, field)
        if items is None:
            return error_response(f"{name} 中不存在可编辑的列表目标")
        return json_response(
            {
                "status": "ok",
                "data": {"file": name, "kind": kind, "name": exp_name, "field": field, "items": items},
            }
        )

    async def _web_external_save(self):
        data = await request.json(default={})
        name = str(data.get("file") or "")
        kind = str(data.get("kind") or "")
        exp_name = str(data.get("name") or "")
        field = str(data.get("field") or "")
        items = data.get("items")
        if not isinstance(items, list):
            return error_response("无效的条目数据")
        full, err = self._resolve_config_file(name)
        if err:
            return error_response(err)
        content = await self.fs.read_file(full)
        if content is None:
            return error_response(f"无法读取 {name}")
        if self._find_external_target(content, kind, exp_name, field) is None:
            return error_response(f"{name} 中不存在可编辑的列表目标")
        try:
            if kind == "array":
                rebuilt = rebuild_ts_array_export(content, exp_name, items)
            else:
                payload = None
                for exp in parse_ts_config_objects(content):
                    if exp["kind"] == "object" and exp["name"] == exp_name:
                        payload = dict(exp["data"])
                        break
                if payload is None:
                    return error_response(f"{name} 中不存在对象 {exp_name}")
                payload[field] = items
                rebuilt = rebuild_ts_config(content, exp_name, payload)
        except ValueError as e:
            return error_response(f"重建 {name} 失败: {e}")
        ok = await self.fs.write_file(full, rebuilt)
        if not ok:
            return error_response(f"写入 {name} 失败")
        return json_response(
            {
                "status": "ok",
                "data": {"file": name, "kind": kind, "name": exp_name, "field": field, "items": items},
            }
        )

    # ------------------------------------------------------------------
    # 壁纸
    # ------------------------------------------------------------------

    async def _web_wallpaper(self):
        device = str(request.query.get("device", "") or "").lower()
        wall_dir_name = "MobileWallpaper" if device == "mobile" else "DesktopWallpaper"

        src_root = getattr(self, "src_root", "")
        detected = bool(src_root)
        if detected and isinstance(self.fs, LocalFileSystem):
            detected = os.path.isdir(src_root) and is_firefly_blog_local(
                os.path.dirname(src_root)
            )
        elif detected:
            detected = await self.fs.exists(src_root)
        if not detected:
            return error_response(
                "未检测到已部署的 Firefly 博客，请先使用插件目录下的 deploy.sh / deploy.ps1 脚本部署博客"
            )

        if isinstance(self.fs, LocalFileSystem):
            wall_dir = os.path.join(src_root, "assets", "images", wall_dir_name)
        else:
            wall_dir = posixpath.join(src_root, "assets", "images", wall_dir_name)

        if not await self.fs.exists(wall_dir):
            return error_response(
                f"未找到壁纸目录 src/assets/images/{wall_dir_name}，请确认博客中已放置壁纸"
            )

        if isinstance(self.fs, LocalFileSystem):
            entries = os.listdir(wall_dir)
            candidates = [
                n
                for n in entries
                if n.lower().endswith(IMAGE_PREVIEW_EXTS)
                and os.path.isfile(os.path.join(wall_dir, n))
            ]
        else:
            sftp = await self.fs.executor.get_sftp()

            async def _is_file(path: str) -> bool:
                try:
                    attrs = await sftp.stat(path)
                    is_dir = getattr(attrs, "is_dir", None)
                    return not (is_dir() if callable(is_dir) else False)
                except Exception:
                    return False

            entries = await sftp.listdir(wall_dir)
            checks = await asyncio.gather(
                *[_is_file(posixpath.join(wall_dir, n)) for n in entries]
            )
            candidates = [
                n
                for n, ok in zip(entries, checks)
                if n.lower().endswith(IMAGE_PREVIEW_EXTS) and ok
            ]
        if not candidates:
            return error_response(
                f"壁纸目录 src/assets/images/{wall_dir_name} 中没有可用的图片"
            )

        chosen = random.choice(candidates)
        full_path = os.path.join(wall_dir, chosen) if isinstance(self.fs, LocalFileSystem) else posixpath.join(wall_dir, chosen)

        try:
            if isinstance(self.fs, LocalFileSystem):
                size = os.path.getsize(full_path)
                if size > IMAGE_PREVIEW_MAX_BYTES:
                    return error_response(
                        f"壁纸过大（{size} 字节），超过 {IMAGE_PREVIEW_MAX_BYTES} 字节上限"
                    )
                with open(full_path, "rb") as f:
                    raw = f.read()
            else:
                attrs = await sftp.stat(full_path)
                if int(getattr(attrs, "size", 0) or 0) > IMAGE_PREVIEW_MAX_BYTES:
                    return error_response(f"壁纸过大，超过 {IMAGE_PREVIEW_MAX_BYTES} 字节上限")
                async with sftp.open(full_path, "rb") as f:
                    raw = await f.read()
        except OSError:
            return error_response(f"无法读取壁纸: {chosen}")

        import base64

        ext = chosen.lower().rsplit(".", 1)[-1]
        mime = "image/svg+xml" if ext == "svg" else "image/" + ext
        data_url = f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")
        return json_response({
            "status": "ok",
            "data": {
                "device": device or "desktop",
                "dir": wall_dir_name,
                "file": chosen,
                "content": data_url,
            },
        })

    # ------------------------------------------------------------------
    # 站点配置管理（src/config 下纯对象配置文件）
    # ------------------------------------------------------------------

    def _resolve_config_file(self, name: str) -> tuple[Optional[str], Optional[str]]:
        """校验配置文件名称并解析到 src/config 下，返回 (路径, 错误)"""
        name = (name or "").strip().replace("\\", "/")
        if not name or os.path.basename(name) != name or not name.endswith(".ts"):
            return None, "非法配置文件名称"
        try:
            return self._resolve_src_path("config/" + name), None
        except ValueError as e:
            return None, f"非法路径: {e}"

    async def _web_config_files(self):
        try:
            config_dir = self._resolve_src_path("config")
        except ValueError as e:
            return error_response(f"非法路径: {e}")
        if not await self.fs.exists(config_dir):
            return error_response("未找到 src/config 目录")

        entries = await self._list_dir_entries(config_dir)
        files = []
        for entry in entries:
            if entry["type"] != "file":
                continue
            name = entry["name"]
            if name.endswith(".ts"):
                try:
                    full = self._resolve_src_path("config/" + name)
                except ValueError:
                    continue
                content = await self.fs.read_file(full)
                if content is None:
                    continue
                parsed = parse_ts_config_object(content)
                info = {
                    "name": name,
                    "parseable": parsed is not None,
                    "size": entry["size"],
                    "mtime": entry["mtime"],
                }
                if parsed:
                    info["export_name"] = parsed["name"]
                    info["keys"] = list(parsed["data"].keys())
                info["group"] = "external" if self._is_external_config(name) else "core"
                files.append(info)
            elif name.lower().endswith((".md", ".txt")):
                files.append(
                    {
                        "name": name,
                        "doc": True,
                        "size": entry["size"],
                        "mtime": entry["mtime"],
                    }
                )
        return json_response({"status": "ok", "data": {"files": files}})

    async def _web_config_file(self):
        if request.method == "GET":
            return await self._read_config_file_handler()
        return await self._write_config_file_handler()

    async def _read_config_file_handler(self):
        name = request.query.get("name", "")
        full, err = self._resolve_config_file(name)
        if err:
            return error_response(err)
        content = await self.fs.read_file(full)
        if content is None:
            return error_response(f"无法读取 {name}")
        parsed = parse_ts_config_object(content)
        if parsed is None:
            return error_response(
                f"{name} 不是纯对象配置（可能包含动态逻辑），请在「文件管理」中直接编辑"
            )
        return json_response({
            "status": "ok",
            "data": {
                "name": name,
                "export_name": parsed["name"],
                "data": parsed["data"],
            },
        })

    async def _write_config_file_handler(self):
        data = await request.json(default={})
        if not isinstance(data, dict):
            return error_response("无效的请求数据")
        name = str(data.get("name") or "")
        export_name = str(data.get("export_name") or "")
        payload = data.get("data")
        if not isinstance(payload, dict):
            return error_response("无效的配置数据")
        full, err = self._resolve_config_file(name)
        if err:
            return error_response(err)
        content = await self.fs.read_file(full)
        if content is None:
            return error_response(f"无法读取 {name}")
        try:
            rebuilt = rebuild_ts_config(content, export_name, payload)
        except ValueError as e:
            return error_response(f"重建失败: {e}")
        ok = await self.fs.write_file(full, rebuilt)
        if not ok:
            return error_response(f"写入失败: {name}")
        return json_response({"status": "ok", "data": {"name": name}})
