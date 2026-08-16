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
import base64
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
    SITE_INFO_FIELDS,
    THEME_ENUMS,
    THEME_GROUPS,
    THEME_MODULE_META,
)
from .filesystem import LocalFileSystem, RemoteFileSystem
from .models import DeployMode
from .ts_parser import (
    clean_raw,
    collect_raw_paths,
    deep_merge,
    extract_types_enums,
    leaf_fields,
    parse_friend_links,
    parse_ts_config_object,
    parse_ts_config_objects,
    rebuild_friends_config,
    rebuild_ts_array_export,
    rebuild_ts_config,
    strip_paths,
)

MAX_FILE_READ_BYTES = 2 * 1024 * 1024

IMAGE_PREVIEW_EXTS = (
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".ico", ".bmp",
)
IMAGE_PREVIEW_MAX_BYTES = 8 * 1024 * 1024

UPLOAD_ALLOWED_EXTS = (
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".ico", ".bmp",
    ".svg", ".pdf",
    ".mp3", ".mp4", ".wav", ".ogg", ".flac",
    ".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".xml",
    ".js", ".ts", ".jsx", ".tsx", ".css", ".scss",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".zip", ".tar", ".gz",
)

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
    ("site-info", "GET", "站点信息（siteConfig.ts 核心字段 + friends.mdx 站点信息）"),
    ("site-info", "POST", "保存站点信息"),
    ("theme-files", "GET", "列出主题配置模块（src/config 全部可编辑配置）"),
    ("theme-file", "GET", "读取主题配置模块数据（含只读字段与枚举）"),
    ("theme-file", "POST", "保存主题配置模块"),
    ("build-deploy", "POST", "启动构建/部署任务（both|build|deploy）"),
    ("build-deploy", "GET", "查询构建/部署任务状态与日志"),
    ("plugin-config", "GET", "获取插件配置（含 schema 元信息，敏感字段不回显）"),
    ("plugin-config", "POST", "保存插件配置（敏感字段留空则不修改）"),
]


def _web_safe(view):
    """Web handler 异常兜底：记录完整堆栈，并把真实错误返回给页面"""

    @functools.wraps(view)
    async def wrapped(*args, **kwargs):
        try:
            return await view(*args, **kwargs)
        except Exception as e:
            logger.exception(f"[Firefly] Web API 处理异常 ({view.__name__}): {e}")
            return error_response("后端处理异常，请查看日志")

    return wrapped


class WebUIMixin:
    """博客管理 WebUI 后端接口（由 FireflyBlogManager 混入）"""

    _CONFIG_BACKUPS_MAX = 10

    def _config_backups_dir(self) -> str:
        try:
            from astrbot.core.star.star_tools import StarTools
            data_dir = str(StarTools.get_data_dir())
            return os.path.join(data_dir, "config_backups")
        except Exception:
            return os.path.join(self.plugin_dir, "_config_backups")

    def _cleanup_old_backups(self, bak_dir: str, base_name: str) -> None:
        try:
            prefix = base_name + "."
            files = sorted(
                [f for f in os.listdir(bak_dir) if f.startswith(prefix) and f.endswith(".bak")],
                reverse=True,
            )
            for old in files[self._CONFIG_BACKUPS_MAX:]:
                try:
                    os.remove(os.path.join(bak_dir, old))
                except OSError:
                    pass
        except Exception:
            pass

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
            "site-info": self._web_site_info,
            "theme-files": self._web_theme_files,
            "theme-file": self._web_theme_file,
            "build-deploy": self._web_build_deploy,
            "plugin-config": self._web_plugin_config,
        }

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------

    def _resolve_src_path(self, rel_path: str) -> str:
        """将网页传入的相对路径安全解析到 src 根目录内

        Raises:
            ValueError: 路径非法（穿越、绝对路径、symlink 越界等）
        """
        rel = (rel_path or "").strip().replace("\\", "/").lstrip("/")
        parts = [p for p in rel.split("/") if p not in ("", ".")]
        if any(p == ".." for p in parts):
            raise ValueError("路径不允许包含 ..")
        src_root = self.src_root
        if isinstance(self.fs, LocalFileSystem):
            full = os.path.normpath(os.path.join(src_root, *parts))
            # 用 realpath 解析 symlink，防止 symlink 指向 src 外
            real_full = os.path.realpath(full)
            real_root = os.path.realpath(src_root)
            if not (real_full == real_root or real_full.startswith(real_root.rstrip("/") + "/")):
                raise ValueError("路径越界（symlink 解析后超出 src 目录）")
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

        display_blog_root = blog_root
        if deploy_mode == DeployMode.REMOTE_BUILD:
            display_blog_root = "[remote]"

        return json_response({
            "status": "ok",
            "data": {
                "version": PLUGIN_VERSION,
                "deploy_mode": deploy_mode.value,
                "blog_root": display_blog_root,
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
    # 插件配置（部署/SSH/构建/功能开关，schema 驱动表单）
    # ------------------------------------------------------------------

    _PLUGIN_CONFIG_SECRET_FIELDS = {"password"}

    def _load_config_schema(self) -> dict:
        schema_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "_conf_schema.json"
        )
        try:
            with open(schema_path, encoding="utf-8-sig") as f:
                schema = json.load(f)
            return schema if isinstance(schema, dict) else {}
        except Exception as e:
            logger.error(f"[Firefly] 读取 _conf_schema.json 失败: {e}")
            return {}

    @staticmethod
    def _coerce_config_value(raw, meta: dict):
        t = meta.get("type")
        if t == "bool":
            if isinstance(raw, bool):
                return raw
            return str(raw).strip().lower() in ("true", "1", "yes", "on")
        if t == "int":
            return int(raw)
        if t == "float":
            return float(raw)
        if t == "list":
            if isinstance(raw, list):
                return raw
            return [x.strip() for x in str(raw).split(",") if x.strip()]
        return str(raw)

    async def _web_plugin_config(self):
        if request.method == "GET":
            return await self._web_plugin_config_get()
        return await self._web_plugin_config_save()

    async def _web_plugin_config_get(self):
        schema = self._load_config_schema()
        values = {}
        for name, meta in schema.items():
            value = self.config.get(name)
            if value is None:
                default = meta.get("default")
                if default is None and meta.get("type") == "string":
                    default = ""
                value = default
            if name in self._PLUGIN_CONFIG_SECRET_FIELDS and value:
                value = ""
            values[name] = value
        return json_response({"status": "ok", "data": {"schema": schema, "values": values}})

    async def _web_plugin_config_save(self):
        data = await request.json(default={})
        payload = data.get("config")
        if not isinstance(payload, dict):
            return error_response("无效的请求数据")
        schema = self._load_config_schema()
        errors = []
        for name, raw in payload.items():
            if name not in schema:
                continue
            if name in self._PLUGIN_CONFIG_SECRET_FIELDS and raw in ("", None):
                continue
            try:
                value = self._coerce_config_value(raw, schema[name])
            except (TypeError, ValueError):
                errors.append(f"{name} 值无效")
                continue
            self.config[name] = value
        if errors:
            return error_response("配置无效: " + "; ".join(errors))
        try:
            await self.config.save_config_async()
        except Exception as e:
            logger.error(f"[Firefly] 保存插件配置失败: {e}")
            return error_response(f"保存插件配置失败: {e}")
        self._init_components()
        return json_response({"status": "ok", "data": {"saved": list(payload.keys())}})

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
        return json_response({"status": "ok", "data": {"path": rel_path or "", "entries": entries, "total": len(entries)}})

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
        has_script = bool(re.search(r"<script[\s>]", content, re.IGNORECASE))
        return json_response({
            "status": "ok",
            "data": {
                "path": rel_path or "",
                "content": content,
                "binary": False,
                "xss_warning": has_script,
            },
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
        if not await self.fs.exists(full):
            return error_response(f"文件不存在: {rel_path}，请先创建文件或通过上传功能上传")
        ok = await self.fs.write_file(full, content)
        if not ok:
            return error_response(f"写入失败: {rel_path}")
        return json_response({"status": "ok", "data": {"path": rel_path or ""}})

    async def _web_file_delete(self):
        data = await request.json(default={})
        rel_path = str(data.get("path") or "")
        confirm = data.get("confirm", False)
        if not rel_path:
            return error_response("缺少路径")
        if not confirm:
            return error_response("缺少确认参数 confirm=true，重复请求以确认删除")
        try:
            full = self._resolve_src_path(rel_path)
        except ValueError as e:
            return error_response(f"非法路径: {e}")
        if full == self.src_root:
            return error_response("不允许删除 src 根目录")
        logger.info(f"[Firefly] 删除文件: {rel_path}")
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
        if not any(filename.lower().endswith(ext) for ext in UPLOAD_ALLOWED_EXTS):
            return error_response(f"不允许上传该类型文件（{os.path.splitext(filename)[1] or '未知'}），仅支持常见图片/媒体/文本/字体/压缩格式")
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
                elif exp["name"] == "site" and name.lower() == "friends.mdx":
                    # friends.mdx 中的站点信息对象（含申请邮箱）整体可编辑
                    targets.append(
                        {
                            "kind": "object",
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
        """在文件中定位对外展示编辑目标，返回数组值或整体对象（field==name 时）或 None"""
        for exp in parse_ts_config_objects(content):
            if kind == "array" and exp["kind"] == "array" and exp["name"] == name:
                return exp["data"]
            if (
                kind == "object"
                and exp["kind"] == "object"
                and exp["name"] == name
            ):
                if field == name:
                    return exp["data"]
                if isinstance(exp["data"].get(field), list):
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
        if not isinstance(items, (list, dict)):
            return error_response("无效的条目数据")
        full, err = self._resolve_config_file(name)
        if err:
            return error_response(err)
        content = await self.fs.read_file(full)
        if content is None:
            return error_response(f"无法读取 {name}")
        if self._find_external_target(content, kind, exp_name, field) is None:
            return error_response(f"{name} 中不存在可编辑的目标")
        try:
            if kind == "array":
                rebuilt = rebuild_ts_array_export(content, exp_name, items)
            elif field == exp_name:
                # 整体对象编辑（如 friends.mdx 的站点信息 site）
                if not isinstance(items, dict):
                    return error_response("对象目标需要完整的对象数据")
                rebuilt = rebuild_ts_config(content, exp_name, items)
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
    # 站点信息（siteConfig.ts 核心字段 + friends.mdx 站点信息）
    # ------------------------------------------------------------------

    async def _web_site_info(self):
        if request.method == "GET":
            return await self._web_site_info_get()
        return await self._web_site_info_save()

    async def _web_site_info_get(self):
        result = {"site_config": None, "site": None}

        full, err = self._resolve_config_file("siteConfig.ts")
        if not err and await self.fs.exists(full):
            content = await self.fs.read_file(full)
            if content is not None:
                for exp in parse_ts_config_objects(content):
                    if exp["kind"] == "object":
                        data = {
                            k: exp["data"][k]
                            for k in SITE_INFO_FIELDS
                            if k in exp["data"]
                        }
                        if data:
                            result["site_config"] = data
                        break

        mdx, mdx_err = self._resolve_config_file("friends.mdx")
        if not mdx_err and await self.fs.exists(mdx):
            content = await self.fs.read_file(mdx)
            if content is not None:
                for exp in parse_ts_config_objects(content):
                    if exp["kind"] == "object" and exp["name"] == "site":
                        result["site"] = exp["data"]
                        break

        return json_response({"status": "ok", "data": result})

    async def _web_site_info_save(self):
        data = await request.json(default={})
        site_config = data.get("site_config")
        site = data.get("site")
        if not isinstance(site_config, dict) and not isinstance(site, dict):
            return error_response("没有可保存的站点信息")
        done = []

        if isinstance(site_config, dict):
            full, err = self._resolve_config_file("siteConfig.ts")
            if err:
                return error_response(err)
            content = await self.fs.read_file(full)
            if content is None:
                return error_response("无法读取 siteConfig.ts")
            payload = None
            exp_name = None
            for exp in parse_ts_config_objects(content):
                if exp["kind"] == "object":
                    payload = dict(exp["data"])
                    exp_name = exp["name"]
                    break
            if payload is None:
                return error_response("siteConfig.ts 中不存在可编辑的纯对象配置")
            for k, v in site_config.items():
                if k in SITE_INFO_FIELDS:
                    payload[k] = v
            try:
                rebuilt = rebuild_ts_config(content, exp_name, payload)
            except ValueError as e:
                return error_response(f"重建 siteConfig.ts 失败: {e}")
            if not await self.fs.write_file(full, rebuilt):
                return error_response("写入 siteConfig.ts 失败")
            done.append("siteConfig.ts")

        if isinstance(site, dict):
            mdx, mdx_err = self._resolve_config_file("friends.mdx")
            if mdx_err:
                return error_response(mdx_err)
            content = await self.fs.read_file(mdx)
            if content is None:
                return error_response("无法读取 friends.mdx")
            payload = None
            for exp in parse_ts_config_objects(content):
                if exp["kind"] == "object" and exp["name"] == "site":
                    payload = dict(exp["data"])
                    break
            if payload is None:
                return error_response("friends.mdx 中不存在 site 对象")
            for k, v in site.items():
                if isinstance(k, str):
                    payload[k] = v
            try:
                rebuilt = rebuild_ts_config(content, "site", payload)
            except ValueError as e:
                return error_response(f"重建 friends.mdx 失败: {e}")
            if not await self.fs.write_file(mdx, rebuilt):
                return error_response("写入 friends.mdx 失败")
            done.append("friends.mdx")

        return json_response(
            {"status": "ok", "data": {"saved": done}}
        )

    # ------------------------------------------------------------------
    # 主题配置可视化（src/config 全部可编辑模块）
    # ------------------------------------------------------------------

    async def _web_theme_files(self):
        files = []
        for meta_file, meta in THEME_MODULE_META.items():
            full, err = self._resolve_config_file(meta_file)
            if err or not await self.fs.exists(full):
                continue
            content = await self.fs.read_file(full)
            if content is None:
                continue
            exports = meta.get("export") or meta_file[:-3]
            if isinstance(exports, str):
                exports = [exports]
            found = {}
            for exp in parse_ts_config_objects(content):
                if exp["name"] in exports:
                    found[exp["name"]] = {
                        "export": exp["name"],
                        "kind": exp["kind"],
                        "readonly": bool(collect_raw_paths(exp["data"])),
                    }
            source_only = [e for e in exports if e not in found]
            files.append(
                {
                    "file": meta_file,
                    "group": meta["group"],
                    "name": meta["name"],
                    "desc": meta["desc"],
                    "exports": [found[e] for e in exports if e in found],
                    "source_only": source_only,
                }
            )
        groups = [{"key": k, "name": n} for k, n in THEME_GROUPS]
        return json_response({"status": "ok", "data": {"groups": groups, "files": files}})

    def _theme_enums_for(self, meta_file: str, data) -> dict:
        """返回字段路径 -> 枚举选项（手动表优先，types 自动提取兜底）"""
        enums = {}
        manual = THEME_ENUMS.get(meta_file, {})
        auto = {}
        types_name = meta_file[: -len(".ts")] if meta_file.endswith(".ts") else meta_file
        types_rel = "types/" + types_name + ".ts"
        try:
            types_full = self._resolve_src_path(types_rel)
            if types_full and isinstance(self.fs, LocalFileSystem) and os.path.isfile(types_full):
                t_content = open(types_full, encoding="utf-8").read()
                auto = extract_types_enums(t_content)
        except (ValueError, OSError):
            auto = {}
        for path in leaf_fields(data):
            if path in manual:
                enums[path] = manual[path]
                continue
            base = path.split(".")[-1]
            if base in auto and base not in enums:
                enums[path] = auto[base]
        return enums

    async def _web_theme_file(self):
        if request.method == "GET":
            return await self._web_theme_file_get()
        return await self._web_theme_file_save()

    async def _web_theme_file_get(self):
        meta_file = str(request.query.get("file", "") or "")
        export = str(request.query.get("export", "") or "")
        full, err = self._resolve_config_file(meta_file)
        if err:
            return error_response(err)
        content = await self.fs.read_file(full)
        if content is None:
            return error_response(f"无法读取 {meta_file}")
        target = None
        for exp in parse_ts_config_objects(content):
            if exp["name"] == export:
                target = exp
                break
        if target is None:
            return error_response(f"{meta_file} 中不存在可编辑导出 {export}")
        readonly = collect_raw_paths(target["data"])
        enums = self._theme_enums_for(meta_file, target["data"])
        return json_response(
            {
                "status": "ok",
                "data": {
                    "file": meta_file,
                    "export": export,
                    "kind": target["kind"],
                    "data": clean_raw(target["data"]),
                    "readonly": readonly,
                    "enums": enums,
                },
            }
        )

    async def _web_theme_file_save(self):
        data = await request.json(default={})
        meta_file = str(data.get("file") or "")
        export = str(data.get("export") or "")
        user_data = data.get("data")
        if user_data is None:
            return error_response("缺少 data 字段")
        full, err = self._resolve_config_file(meta_file)
        if err:
            return error_response(err)
        content = await self.fs.read_file(full)
        if content is None:
            return error_response(f"无法读取 {meta_file}")
        target = None
        for exp in parse_ts_config_objects(content):
            if exp["name"] == export:
                target = exp
                break
        if target is None:
            return error_response(f"{meta_file} 中不存在可编辑导出 {export}")
        try:
            if target["kind"] == "array":
                if not isinstance(user_data, list):
                    return error_response("数组导出需要列表数据")
                rebuilt = rebuild_ts_array_export(content, export, user_data)
            else:
                if not isinstance(user_data, dict):
                    return error_response("对象导出需要对象数据")
                readonly = collect_raw_paths(target["data"])
                safe_user = strip_paths(user_data, readonly)
                merged = deep_merge(target["data"], safe_user)
                rebuilt = rebuild_ts_config(content, export, merged)
        except ValueError as e:
            return error_response(f"重建 {meta_file} 失败: {e}")
        try:
            bak_dir = self._config_backups_dir()
            os.makedirs(bak_dir, exist_ok=True)
            from datetime import datetime as _dt
            ts = _dt.now().strftime("%Y%m%d_%H%M%S")
            bak_name = os.path.basename(full) + f".{ts}.bak"
            bak = os.path.join(bak_dir, bak_name)
            if isinstance(self.fs, LocalFileSystem):
                with open(bak, "w", encoding="utf-8") as f:
                    f.write(content)
            else:
                await self.fs.write_file(bak, content)
            self._cleanup_old_backups(bak_dir, os.path.basename(full))
        except Exception:
            pass
        if not await self.fs.write_file(full, rebuilt):
            return error_response(f"写入 {meta_file} 失败")
        return json_response(
            {"status": "ok", "data": {"file": meta_file, "export": export, "saved": True}}
        )

    # ------------------------------------------------------------------
    # 构建 / 部署任务（状态页按钮）
    # ------------------------------------------------------------------

    _BUILD_TASKS_MAX = 20

    def _build_tasks_store(self) -> dict:
        if not hasattr(self, "_build_tasks"):
            self._build_tasks = {}
            self._build_task_seq = 0
        if len(self._build_tasks) > self._BUILD_TASKS_MAX:
            keys = sorted(self._build_tasks.keys())
            for k in keys[: len(keys) - self._BUILD_TASKS_MAX]:
                self._build_tasks.pop(k, None)
        return self._build_tasks

    async def _web_build_deploy(self):
        if request.method == "POST":
            return await self._web_build_deploy_start()
        return await self._web_build_deploy_query()

    async def _web_build_deploy_start(self):
        data = await request.json(default={})
        mode = str(data.get("mode") or "both")
        if mode not in ("both", "build", "deploy"):
            return error_response("无效的构建模式")
        if getattr(self, "build_manager", None) is None:
            return error_response("构建管理器未初始化（未检测到已部署的博客），请先在状态页配置博客目录")
        store = self._build_tasks_store()
        running = any(t.get("status") == "running" for t in store.values())
        if running:
            return error_response("已有构建任务进行中，请等待完成后再发起新任务")
        self._build_task_seq += 1
        task_id = self._build_task_seq
        store[task_id] = {"status": "running", "log": [], "message": "", "ok": None}
        asyncio.create_task(self._run_build_task(task_id, mode))
        return json_response({"status": "ok", "data": {"task_id": task_id, "mode": mode}})

    _BUILD_TASK_TIMEOUT = 3600

    async def _run_build_task(self, task_id: int, mode: str) -> None:
        store = self._build_tasks_store()
        task = store[task_id]
        steps = []

        def upd(line: str) -> None:
            steps.append(line)
            task["log"] = list(steps)

        try:
            bm = self.build_manager
            if bm is None:
                raise RuntimeError("构建管理器未初始化")

            async def _do_build():
                if mode in ("both", "build"):
                    upd("正在检查构建环境...")
                    ok, msg = await bm.check_environment()
                    if not ok:
                        raise RuntimeError(f"环境检查失败: {msg}")
                    upd(f"[OK] 环境检查: {msg}")
                    if mode == "both" and not await bm.check_dependencies_installed():
                        upd("依赖未安装，正在安装（可能需要几分钟）...")
                        ok, msg = await bm.install_dependencies()
                        if not ok:
                            raise RuntimeError(f"依赖安装失败: {msg}")
                        upd(f"[OK] 依赖安装: {msg}")
                    upd("正在构建博客（pnpm build）...")
                    ok, msg = await bm.build()
                    if not ok:
                        raise RuntimeError(f"构建失败: {msg}")
                    upd(f"[OK] 构建: {msg}")
                if mode in ("both", "deploy"):
                    upd("正在部署...")
                    ok, msg = await bm.deploy()
                    if not ok:
                        raise RuntimeError(f"部署失败: {msg}")
                    upd(f"[OK] 部署: {msg}")

            await asyncio.wait_for(_do_build(), timeout=self._BUILD_TASK_TIMEOUT)
            task["status"] = "done"
            task["ok"] = True
            task["message"] = "\n".join(steps)
        except asyncio.TimeoutError:
            task["status"] = "done"
            task["ok"] = False
            task["message"] = "\n".join(steps) + f"\n[ERROR] 构建任务超时（超过 {self._BUILD_TASK_TIMEOUT} 秒）"
        except Exception as e:
            task["status"] = "done"
            task["ok"] = False
            task["message"] = "\n".join(steps) + (f"\n[ERROR] {e}" if steps else str(e))

    async def _web_build_deploy_query(self):
        store = self._build_tasks_store()
        tid = request.query.get("task_id", "")
        if tid and tid.isdigit():
            task = store.get(int(tid))
            if task is None:
                return error_response("任务不存在或已过期")
            return json_response({"status": "ok", "data": {"task": task}})
        latest = max(store.items(), key=lambda kv: kv[0]) if store else None
        return json_response(
            {
                "status": "ok",
                "data": {
                    "task": dict(latest[1], task_id=latest[0]) if latest else None
                },
            }
        )

    # ------------------------------------------------------------------
    # 站点配置管理（src/config 下纯对象配置文件）
    # ------------------------------------------------------------------

    def _resolve_config_file(self, name: str) -> tuple[Optional[str], Optional[str]]:
        """校验配置文件名称并解析到博客 src 下，返回 (路径, 错误)

        普通配置文件位于 src/config 下（*.ts）；friends.mdx 为对外展示中的
        站点信息文件，位于 src/content/spec 下。
        """
        name = (name or "").strip().replace("\\", "/")
        if not name or os.path.basename(name) != name:
            return None, "非法配置文件名称"
        if name.lower() == "friends.mdx":
            try:
                return self._resolve_src_path("content/spec/friends.mdx"), None
            except ValueError as e:
                return None, f"非法路径: {e}"
        if not name.endswith(".ts"):
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
        files.sort(key=lambda f: (f.get("group", "") != "core", f["name"].lower()))
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
