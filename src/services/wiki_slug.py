"""wiki-first 文件系统层共用工具:slug 生成 + frontmatter 读写。"""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

import yaml

_UNSAFE_RE = re.compile(r"[^\w一-鿿\-]+")
_WS_RE = re.compile(r"\s+")


def slugify(title: str) -> str:
    """标题 → 文件名安全 slug。

    小写、标点去除(转空格)、空格转连字符、合并连续连字符;中文/字母/数字/连字符保留。
    """
    if not title:
        return "untitled"
    cleaned = _UNSAFE_RE.sub(" ", title).strip().lower()
    slug = _WS_RE.sub("-", cleaned)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or "untitled"


def resolve_slug(dir_path: Path, title: str, source_hash: str) -> tuple[str, Path]:
    """解析最终 slug,处理同名冲突。

    - 文件不存在 → (slugify(title), <slug>.md)
    - 已存在且 frontmatter source_hash 相同 → 返回同路径(幂等覆盖)
    - 已存在但 hash 不同 → 追加 ``-{hash[:8]}``
    """
    base = slugify(title)
    candidate = dir_path / f"{base}.md"
    if not candidate.exists():
        return base, candidate
    existing = read_frontmatter(candidate).get("source_hash", "")
    # 仅当 source_hash 非空且一致才走幂等覆盖。两者皆空时(``"" == ""``)不得判为
    # 幂等——否则空 hash 条目(如 mcp_server.create 的非文件路径)会覆盖一个不相关
    # 的同名源页。空 hash 一律走冲突后缀路径。
    if source_hash and existing == source_hash:
        return base, candidate
    suffix = (source_hash[:8]) if source_hash else "dup"
    conflicted = dir_path / f"{base}-{suffix}.md"
    return f"{base}-{suffix}", conflicted


def read_frontmatter(path: Path) -> dict:
    """读取 markdown frontmatter(``---`` 之间的 YAML)。无则返回 {}。"""
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    try:
        data = yaml.safe_load(parts[1])
        return data if isinstance(data, dict) else {}
    except yaml.YAMLError:
        return {}


def write_markdown(path: Path, frontmatter: dict, body: str) -> None:
    """原子写入 frontmatter + body。

    先写同目录临时文件再 ``os.replace``(同文件系统原子替换),避免 ``write_text``
    中途崩溃留下半写的损坏文件(旧实现注释称原子,实为直接 ``write_text`` 非原子)。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fm = yaml.safe_dump(
        frontmatter, allow_unicode=True, default_flow_style=False, sort_keys=False
    )
    content = f"---\n{fm}---\n\n{body}\n"
    tmp_fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".md.tmp")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
