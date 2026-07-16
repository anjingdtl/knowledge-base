"""统一路径管理 — 适配源码运行和 pip 安装两种模式"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def get_project_root() -> Path:
    """获取项目根目录。

    优先级:
    1. SHINEHE_HOME 环境变量（自定义部署）
    2. 包安装模式 → ~/.shinehe-knowledge/
    3. 源码开发模式 → 代码仓库根目录
    """
    env_root = os.environ.get("SHINEHE_HOME")
    if env_root:
        return Path(env_root).resolve()

    # 检测是否在 site-packages 中运行
    package_dir = Path(__file__).resolve().parent.parent.parent  # src/utils/ -> src/ -> package_root
    site_markers = ("site-packages", "dist-packages", ".pex")
    if any(m in str(package_dir) for m in site_markers):
        home = Path.home() / ".shinehe-knowledge"
        home.mkdir(parents=True, exist_ok=True)
        return home

    # 源码模式
    return package_dir


def get_config_path() -> Path:
    """配置文件路径，首次安装自动生成默认配置"""
    root = get_project_root()
    config = root / "config.yaml"
    if not config.exists():
        _generate_default_config(config)
    return config


def _is_absolute_path(value: str | Path) -> bool:
    p = Path(value)
    return p.is_absolute() or (len(str(value)) > 1 and str(value)[1] == ":")


def resolve_storage_paths(
    *,
    config_path: str | Path | None = None,
    shinehe_home: str | Path | None = None,
    storage_data_dir: str | Path | None = None,
    db_name: str = "kb.db",
    graph_dir: str | Path | None = None,
) -> dict[str, Path]:
    """统一解析 data/db/graph/vector 存储路径。

    规则:
    - storage.data_dir 若为绝对路径，直接使用（不被 SHINEHE_HOME 覆盖）
    - 相对路径则相对于 shinehe_home / project root
    - sqlite-vec 向量索引内嵌于 DB，storage_path 等于 data_dir
    """
    home = Path(shinehe_home).resolve() if shinehe_home else get_project_root()
    if config_path is None:
        config_path = home / "config.yaml"
    cfg_path = Path(config_path)

    data_raw = storage_data_dir
    graph_raw = graph_dir
    db_raw = db_name
    if data_raw is None and cfg_path.is_file():
        try:
            import yaml

            with open(cfg_path, encoding="utf-8") as f:
                cfg: dict[str, Any] = yaml.safe_load(f) or {}
            storage = cfg.get("storage") or {}
            data_raw = storage.get("data_dir", "data")
            graph_raw = graph_raw or storage.get("graph_dir", "graph")
            db_raw = storage.get("db_name", db_name)
        except Exception:
            data_raw = "data"

    if data_raw is None:
        data_raw = "data"
    data_path = Path(str(data_raw))
    if _is_absolute_path(data_path):
        data_dir = data_path.resolve()
    else:
        data_dir = (home / data_path).resolve()

    db_path = data_dir / str(db_raw or "kb.db")

    if graph_raw is None:
        graph_raw = "graph"
    graph_path = Path(str(graph_raw))
    if _is_absolute_path(graph_path):
        graph_resolved = graph_path.resolve()
    else:
        # graph_dir often relative to data_dir historically
        graph_resolved = (data_dir / graph_path).resolve()

    return {
        "home": home,
        "config_path": cfg_path.resolve() if cfg_path.exists() else cfg_path,
        "data_dir": data_dir,
        "db_path": db_path,
        "graph_dir": graph_resolved,
        "vector_storage_path": data_dir,
    }


def resolve_vector_storage_path(
    *,
    config_path: str | Path | None = None,
    shinehe_home: str | Path | None = None,
    storage_data_dir: str | Path | None = None,
    vector_backend: str = "sqlite-vec",
) -> Path:
    """向量索引存储路径（sqlite-vec 与 DB 同目录）。"""
    _ = vector_backend  # reserved for chroma/external backends
    paths = resolve_storage_paths(
        config_path=config_path,
        shinehe_home=shinehe_home,
        storage_data_dir=storage_data_dir,
    )
    return paths["vector_storage_path"]


def get_data_dir() -> Path:
    """数据目录路径（相对默认 data；绝对 storage.data_dir 由 Config 解析）。"""
    root = get_project_root()
    data_dir = root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def _generate_default_config(path: Path):
    """生成默认配置文件"""
    import yaml

    default = {
        "embedding": {
            "api_key": "YOUR_EMBEDDING_API_KEY",
            "base_url": "https://api.siliconflow.cn/v1",
            "model": "BAAI/bge-m3",
            "reuse_llm": True,
        },
        "llm": {
            "api_key": "YOUR_LLM_API_KEY",
            "base_url": "https://api.minimaxi.com/v1",
            "model": "MiniMax-M2.7",
            "temperature": 0.7,
            "max_tokens": 2048,
        },
        "rag": {
            "chunk_overlap": 150,
            "chunk_size": 1000,
            "score_threshold": 0.35,
            "top_k": 5,
        },
        "storage": {
            "data_dir": "data",
            "db_name": "kb.db",
            "graph_dir": "graph",
        },
    }
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(default, f, allow_unicode=True, default_flow_style=False)
