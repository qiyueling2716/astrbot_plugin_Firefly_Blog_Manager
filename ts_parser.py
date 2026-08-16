"""TypeScript 配置文件解析与重建工具。

解析 Firefly 博客 src/config/ 下的纯字面量导出（对象/数组），
支持读取、修改、重建，保留文件其余部分和不可解析的 __raw__ 字段。
"""

from __future__ import annotations

import json
import re
from typing import Optional


# ============================================================================
# 底层解析工具
# ============================================================================

def strip_ts_comments(text: str) -> str:
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


def find_matching_bracket(text: str, open_idx: int, open_ch: str, close_ch: str) -> int:
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


def split_top_level(text: str) -> list[str]:
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


def find_char_skip_comments(text: str, start: int, target: str) -> int:
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


# ============================================================================
# 值解析
# ============================================================================

def _decode_ts_single_quote(s: str) -> Optional[str]:
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


def parse_ts_value(raw: str):
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
        close_idx = find_matching_bracket(value, 0, "{", "}")
        if close_idx == -1:
            return {"__raw__": value}
        return _parse_ts_object_body(value[1:close_idx])
    if value.startswith("["):
        close_idx = find_matching_bracket(value, 0, "[", "]")
        if close_idx == -1:
            return {"__raw__": value}
        items = []
        for part in split_top_level(value[1:close_idx]):
            if part.strip():
                items.append(parse_ts_value(part))
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
    data = {}
    for pair in split_top_level(body):
        colon = pair.find(":")
        if colon == -1:
            continue
        key = pair[:colon].strip().strip('"').strip("'")
        if not key:
            continue
        data[key] = parse_ts_value(pair[colon + 1 :])
    return data


# ============================================================================
# JS friendsConfig 解析 / 重建
# ============================================================================

def _decode_js_value(raw: str):
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
        for part in split_top_level(inner):
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


def js_repr(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(js_repr(v) for v in value) + "]"
    if isinstance(value, dict) and "__raw__" in value:
        return value["__raw__"]
    return json.dumps(str(value), ensure_ascii=False)


KNOWN_LINK_KEYS = ["title", "imgurl", "desc", "siteurl", "tags", "weight", "enabled"]


def parse_friend_links(content: str) -> tuple[list[dict], Optional[str]]:
    text = strip_ts_comments(content)
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
    close_idx = find_matching_bracket(text, open_idx, "[", "]")
    if close_idx == -1:
        return [], "friendsConfig 数组未闭合"

    array_body = text[open_idx + 1 : close_idx]
    links = []
    for chunk in split_top_level(array_body):
        chunk = chunk.strip()
        if not chunk:
            continue
        if not chunk.startswith("{"):
            continue
        link = {}
        body = chunk[1:].rsplit("}", 1)[0] if chunk.endswith("}") else chunk[1:]
        for pair in split_top_level(body):
            colon = pair.find(":")
            if colon == -1:
                continue
            key = pair[:colon].strip().strip('"').strip("'")
            raw_value = pair[colon + 1 :]
            link[key] = _decode_js_value(raw_value)
        links.append(link)
    return links, None


def rebuild_friends_config(content: str, links: list[dict]) -> str:
    text = strip_ts_comments(content)
    anchor = text.find("export const friendsConfig")
    if anchor == -1:
        anchor = text.find("friendsConfig")
    if anchor == -1:
        raise ValueError("未找到 friendsConfig 定义")
    raw_anchor = content.find("export const friendsConfig")
    if raw_anchor == -1:
        raw_anchor = content.find("friendsConfig")
    eq_idx = content.find("=", raw_anchor)
    if eq_idx == -1:
        raise ValueError("未找到 friendsConfig 赋值")
    open_idx = content.find("[", eq_idx)
    if open_idx == -1:
        raise ValueError("未找到 friendsConfig 数组")
    close_idx = find_matching_bracket(content, open_idx, "[", "]")
    if close_idx == -1:
        raise ValueError("friendsConfig 数组未闭合")

    lines = ["["]
    for link in links:
        lines.append("\t{")
        for key in KNOWN_LINK_KEYS:
            if key in link:
                lines.append(f"\t\t{key}: {js_repr(link[key])},")
        for key, value in link.items():
            if key not in KNOWN_LINK_KEYS:
                lines.append(f"\t\t{key}: {js_repr(value)},")
        lines.append("\t},")
    lines.append("]")
    block = "\n".join(lines)

    return content[:open_idx] + block + content[close_idx + 1 :]


# ============================================================================
# TS 配置对象解析 / 重建
# ============================================================================

def parse_ts_config_objects(content: str) -> list[dict]:
    text = strip_ts_comments(content)
    results = []
    for m in re.finditer(r"export\s+const\s+([A-Za-z_$][\w$]*)", text):
        name = m.group(1)
        eq_idx = text.find("=", m.end())
        if eq_idx == -1:
            continue
        rest = text[eq_idx + 1 :].lstrip()
        if rest.startswith("["):
            data = parse_ts_value(rest)
            if isinstance(data, list):
                results.append({"name": name, "kind": "array", "data": data})
        elif rest.startswith("{"):
            open_idx = text.find("{", eq_idx)
            if open_idx == -1:
                continue
            between_end = text[eq_idx + 1 : open_idx].strip()
            if between_end and not between_end.startswith(":"):
                continue
            close_idx = find_matching_bracket(text, open_idx, "{", "}")
            if close_idx == -1:
                continue
            data = _parse_ts_object_body(text[open_idx + 1 : close_idx])
            if data is not None:
                results.append({"name": name, "kind": "object", "data": data})
    return results


def parse_ts_config_object(content: str) -> Optional[dict]:
    for exp in parse_ts_config_objects(content):
        if exp["kind"] == "object":
            return {"name": exp["name"], "data": exp["data"]}
    return None


# ============================================================================
# 数据工具函数
# ============================================================================

def collect_raw_paths(data, prefix: str = "") -> dict:
    paths = {}
    if isinstance(data, dict):
        for k, v in data.items():
            p = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict) and "__raw__" in v and len(v) == 1:
                paths[p] = str(v["__raw__"])
            elif isinstance(v, dict):
                paths.update(collect_raw_paths(v, p))
            elif isinstance(v, list):
                paths.update(collect_raw_paths(v, p))
    elif isinstance(data, list):
        for it in data:
            if isinstance(it, dict) and "__raw__" in it and len(it) == 1:
                paths[f"{prefix}[]"] = str(it["__raw__"])
            elif isinstance(it, (dict, list)):
                paths.update(collect_raw_paths(it, f"{prefix}[]"))
    return paths


def clean_raw(data):
    if isinstance(data, dict):
        if "__raw__" in data and len(data) == 1:
            return str(data["__raw__"])
        return {k: clean_raw(v) for k, v in data.items()}
    if isinstance(data, list):
        return [clean_raw(v) for v in data]
    return data


def strip_paths(data, paths, prefix: str = ""):
    if isinstance(data, dict):
        out = {}
        for k, v in data.items():
            p = f"{prefix}.{k}" if prefix else k
            if p in paths:
                continue
            if isinstance(v, list) and f"{p}[]" in paths:
                continue
            out[k] = strip_paths(v, paths, p)
        return out
    if isinstance(data, list):
        return [strip_paths(it, paths, f"{prefix}[]") for it in data]
    return data


def deep_merge(base, override):
    if isinstance(base, dict) and isinstance(override, dict):
        out = dict(base)
        for k, v in override.items():
            out[k] = deep_merge(base.get(k), v) if k in base else v
        return out
    return override


def leaf_fields(data, prefix: str = "") -> list:
    paths = []
    if isinstance(data, dict):
        for k, v in data.items():
            p = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict):
                paths.extend(leaf_fields(v, p))
            elif isinstance(v, list):
                if v and isinstance(v[0], dict):
                    paths.extend(leaf_fields(v[0], f"{p}[]"))
                else:
                    paths.append(p)
            else:
                paths.append(p)
    return paths


def extract_types_enums(text: str) -> dict:
    enums = {}
    t = strip_ts_comments(text)
    for m in re.finditer(
        r'([A-Za-z_$][\w$]*)\??:\s*("(?:[^"\\]|\\.)*"(?:\s*\|\s*(?:"(?:[^"\\]|\\.)*"))+)\s*;',
        t,
    ):
        opts = re.findall(r'"((?:[^"\\]|\\.)*)"', m.group(2))
        if opts:
            enums[m.group(1)] = opts
    return enums


# ============================================================================
# TS 重建
# ============================================================================

def _rebuild_ts_value(value, level: int) -> str:
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
        return "[" + ", ".join(js_repr(v) for v in value) + "]"
    return js_repr(value)


def rebuild_ts_config(content: str, export_name: str, data: dict) -> str:
    raw_m = re.search(r"export\s+const\s+" + re.escape(export_name) + r"\b", content)
    if not raw_m:
        raise ValueError(f"未找到 {export_name} 定义")
    eq_idx = find_char_skip_comments(content, raw_m.end(), "=")
    if eq_idx == -1:
        raise ValueError(f"未找到 {export_name} 赋值")
    open_idx = find_char_skip_comments(content, eq_idx + 1, "{")
    if open_idx == -1:
        raise ValueError(f"未找到 {export_name} 对象")
    between = content[eq_idx + 1 : open_idx].strip()
    if between and not between.startswith(":"):
        raise ValueError(f"{export_name} 不是纯对象配置")
    close_idx = find_matching_bracket(content, open_idx, "{", "}")
    if close_idx == -1:
        raise ValueError(f"{export_name} 对象未闭合")
    block = _rebuild_ts_value(data, 0)
    return content[:open_idx] + block + content[close_idx + 1 :]


def rebuild_ts_array_export(content: str, export_name: str, items: list) -> str:
    raw_m = re.search(r"export\s+const\s+" + re.escape(export_name) + r"\b", content)
    if not raw_m:
        raise ValueError(f"未找到 {export_name} 定义")
    eq_idx = find_char_skip_comments(content, raw_m.end(), "=")
    if eq_idx == -1:
        raise ValueError(f"未找到 {export_name} 赋值")
    open_idx = find_char_skip_comments(content, eq_idx + 1, "[")
    if open_idx == -1:
        raise ValueError(f"未找到 {export_name} 数组")
    between = content[eq_idx + 1 : open_idx].strip()
    if between and not between.startswith(":"):
        raise ValueError(f"{export_name} 不是纯数组导出")
    close_idx = find_matching_bracket(content, open_idx, "[", "]")
    if close_idx == -1:
        raise ValueError(f"{export_name} 数组未闭合")
    block = _rebuild_ts_array(items, 0)
    return content[:open_idx] + block + content[close_idx + 1 :]


def _rebuild_ts_array(items: list, level: int = 0) -> str:
    indent = "\t" * level
    child_indent = "\t" * (level + 1)
    if not items:
        return "[]"
    lines = ["["]
    for item in items:
        lines.append(child_indent + _rebuild_ts_value(item, level + 1) + ",")
    lines.append(indent + "]")
    return "\n".join(lines)
