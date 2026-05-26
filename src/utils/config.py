"""配置管理模块"""
import yaml
from pathlib import Path
from src.utils.paths import get_config_path, get_data_dir


class Config:
    _instance = None
    _config: dict = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def load(cls, config_path: str | None = None):
        if config_path is None:
            config_path = str(get_config_path())
        with open(config_path, "r", encoding="utf-8") as f:
            cls._config = yaml.safe_load(f) or {}
        return cls._config

    @classmethod
    def get(cls, key: str, default=None):
        if not cls._config:
            cls.load()
        keys = key.split(".")
        val = cls._config
        for k in keys:
            if isinstance(val, dict):
                val = val.get(k)
            else:
                return default
            if val is None:
                return default
        return val

    @classmethod
    def set(cls, key: str, value):
        if not cls._config:
            cls.load()
        keys = key.split(".")
        cfg = cls._config
        for k in keys[:-1]:
            cfg = cfg.setdefault(k, {})
        cfg[keys[-1]] = value

    @classmethod
    def save(cls, config_path: str | None = None):
        if config_path is None:
            config_path = str(get_config_path())
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(cls._config, f, allow_unicode=True, default_flow_style=False)

    @classmethod
    def get_all(cls) -> dict:
        if not cls._config:
            cls.load()
        return cls._config

    @classmethod
    def get_data_dir(cls) -> Path:
        return get_data_dir()

    @classmethod
    def get_db_path(cls) -> Path:
        return cls.get_data_dir() / cls.get("storage.db_name", "kb.db")

    @classmethod
    def get_chroma_dir(cls) -> Path:
        path = cls.get_data_dir() / cls.get("storage.chroma_dir", "chroma")
        path.mkdir(parents=True, exist_ok=True)
        return path
