"""统一路径管理 — 适配源码运行和 pip 安装两种模式"""
import os
from pathlib import Path


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


def get_data_dir() -> Path:
    """数据目录路径"""
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
