"""路径索引相关数据模型"""
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class FileFingerprint:
    """文件指纹 — 用于快速变更检测"""
    path: Path
    size: int
    mtime_ns: int
    sha256: str


@dataclass
class IndexResult:
    """索引操作结果统计"""
    created: int = 0
    updated: int = 0
    skipped: int = 0
    deleted: int = 0
    failed: list[dict] = field(default_factory=list)
    job_id: str | None = None
    mode: str = "sync"  # "sync" or "async"


@dataclass
class ManifestDiff:
    """目录扫描与索引之间的差异"""
    created: list[FileFingerprint] = field(default_factory=list)
    modified: list[FileFingerprint] = field(default_factory=list)
    unchanged: list[FileFingerprint] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)  # normalized paths
