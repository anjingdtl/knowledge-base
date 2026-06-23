"""配置管理模块

支持两种使用方式:
    1. 实例模式（DI 推荐）: config = Config(); config.load(); config.get("key")
    2. 类方法模式（兼容旧代码）: Config.load(); Config.get("key")

通过 _dualmethod 描述符实现同一方法名在类/实例上的不同派发。

敏感凭据（api_key）通过 OS keychain（keyring）安全存储，不写入 config.yaml。
"""
import functools
import logging
import os
from pathlib import Path
from typing import Any

import yaml

from src.utils.paths import get_config_path, get_data_dir

logger = logging.getLogger(__name__)

# keyring 服务名，用于 OS keychain 中隔离本应用的凭据
_KEYRING_SERVICE = "ShineHeKnowledge"

# 需要通过 keyring 安全存储的配置键
_SECRET_KEYS = {
    "llm.api_key",
    "embedding.api_key",
    "reranker.api_key",
    "api.jwt_secret",
}

# 敏感配置对应的环境变量映射（keyring 不可用时作为替代）
_ENV_KEY_MAP = {
    "llm.api_key": "SHINEHE_LLM_API_KEY",
    "embedding.api_key": "SHINEHE_EMBEDDING_API_KEY",
    "reranker.api_key": "SHINEHE_RERANKER_API_KEY",
    "api.jwt_secret": "SHINEHE_JWT_SECRET",
}

_ENV_FALLBACK_KEY_MAP = {
    "embedding.api_key": ("SHINEHE_LLM_API_KEY",),
}

# keyring 可用性标志
_keyring_available = False
try:
    import keyring
    # 检测 keyring 后端是否可用（有些环境可能没有可用的后端）
    keyring.get_keyring()
    _keyring_available = True
except Exception:
    logger.debug("keyring 库不可用，API Key 将存储在 config.yaml 中")


def _secret_to_keyring_key(key: str) -> str:
    """将点分隔的配置键转换为 keyring 的用户名格式"""
    return key.replace(".", "/")


class _dualmethod:
    """描述符: 类上调用时委托给默认实例，实例上调用时绑定到实例自身

    用法:
        class Foo:
            @_dualmethod
            def bar(self, x):
                return self._data[x]

        Foo.bar("key")      # → Foo._default.bar("key")
        foo = Foo(); foo.bar("key")  # → foo.bar("key")
    """
    def __init__(self, fn):
        self.fn = fn
        functools.update_wrapper(self, fn)

    def __get__(self, obj, objtype=None):
        if obj is None:
            # 类调用: Config.get("key") → 委托到默认实例
            inst = objtype._get_default()
            return self.fn.__get__(inst, objtype)
        else:
            # 实例调用: config.get("key") → 绑定到实例
            return self.fn.__get__(obj, objtype)


class Config:
    """配置管理器

    实例化后通过 get/set 操作配置数据。
    类方法模式（Config.get）保持向后兼容，操作全局默认实例。
    """

    _default_instance: "Config | None" = None

    def __init__(self):
        self._data: dict = {}

    @classmethod
    def _get_default(cls) -> "Config":
        if cls._default_instance is None:
            inst = cls.__new__(cls)
            inst._data = {}
            cls._default_instance = inst
        return cls._default_instance

    # --- 核心方法（@_dualmethod 自动派发） ---

    def _set_nested(self, key: str, value):
        """内部辅助：直接在 _data 中设置嵌套键（不触发 load 检查）"""
        keys = key.split(".")
        cfg: dict[str, Any] = self._data
        for k in keys[:-1]:
            cfg = cfg.setdefault(k, {})
        cfg[keys[-1]] = value

    def _get_nested(self, key: str):
        """内部辅助：直接从 _data 中读取嵌套键"""
        keys = key.split(".")
        val: Any = self._data
        for k in keys:
            if isinstance(val, dict):
                val = val.get(k)
            else:
                return None
            if val is None:
                return None
        return val

    @_dualmethod
    def load(self, config_path: str | None = None) -> dict:
        """加载配置文件，并从 keychain 恢复敏感凭据"""
        if config_path is None:
            config_path = str(get_config_path())
        with open(config_path, "r", encoding="utf-8") as f:
            self._data = yaml.safe_load(f) or {}
        # 从环境/keychain 恢复敏感凭据，优先级: 显式环境变量 → 环境兜底 → keyring。
        # 环境变量覆盖 keyring 可避免 Windows Service/CLI 继续使用 keyring 中的旧 key。
        for secret_key in _SECRET_KEYS:
            value = None
            # 1. 显式环境变量优先
            env_key = _ENV_KEY_MAP.get(secret_key)
            if env_key:
                value = os.environ.get(env_key)
            # 2. 共享环境变量兜底（如 embedding 复用 LLM key）
            if value is None:
                for fallback_env_key in _ENV_FALLBACK_KEY_MAP.get(secret_key, ()):
                    value = os.environ.get(fallback_env_key)
                    if value is not None:
                        break
            # 3. 环境变量未设置时尝试从 keyring 读取
            if value is None and _keyring_available:
                kr_key = _secret_to_keyring_key(secret_key)
                try:
                    value = keyring.get_password(_KEYRING_SERVICE, kr_key)
                except Exception as exc:
                    logger.debug("从 keyring 读取 %s 失败: %s", secret_key, exc)
            # 写入内存中的 _data
            if value is not None:
                self._set_nested(secret_key, value)
        # 实例加载时注册为默认
        Config._default_instance = self
        return self._data

    @_dualmethod
    def get(self, key: str, default=None):
        """点号分隔的嵌套键读取，如 'llm.api_key'"""
        if not self._data:
            self.load()
        keys = key.split(".")
        val: Any = self._data
        for k in keys:
            if isinstance(val, dict):
                val = val.get(k)
            else:
                return default
            if val is None:
                return default
        return val

    @_dualmethod
    def set(self, key: str, value):
        """设置配置值（内存中，需调用 save 持久化）"""
        if not self._data:
            self.load()
        keys = key.split(".")
        cfg = self._data
        for k in keys[:-1]:
            cfg = cfg.setdefault(k, {})
        cfg[keys[-1]] = value

    @_dualmethod
    def save(self, config_path: str | None = None):
        stored_in_keyring = set()
        """持久化配置到文件。敏感凭据写入 OS keychain，不落盘 config.yaml"""
        if config_path is None:
            config_path = str(get_config_path())

        # 将敏感凭据写入 keychain
        if _keyring_available:
            for secret_key in _SECRET_KEYS:
                secret_val = self._get_nested(secret_key)
                kr_key = _secret_to_keyring_key(secret_key)
                try:
                    if secret_val:
                        keyring.set_password(_KEYRING_SERVICE, kr_key, secret_val)
                        stored_in_keyring.add(secret_key)
                    else:
                        # 值为空则清除 keychain 中对应条目
                        try:
                            keyring.delete_password(_KEYRING_SERVICE, kr_key)
                        except keyring.errors.PasswordDeleteError:
                            pass
                        stored_in_keyring.add(secret_key)
                except Exception as exc:
                    logger.warning("写入 keyring %s 失败，将回退到文件存储: %s", secret_key, exc)
        else:
            # keyring 不可用时，凭据将照旧写入 config.yaml 明文文件
            # 检查是否有敏感值即将被写入，有则发出警告
            for secret_key in _SECRET_KEYS:
                secret_val = self._get_nested(secret_key)
                if secret_val:
                    env_key = _ENV_KEY_MAP.get(secret_key, "")
                    logger.warning(
                        "keyring 不可用，敏感配置 '%s' 将以明文存储在 config.yaml 中。"
                        "建议设置环境变量 %s 作为替代",
                        secret_key, env_key,
                    )
                    break  # 只警告一次

        # 写入 YAML 文件时，剥离已通过 keyring 存储的敏感字段
        import copy
        data_to_dump = copy.deepcopy(self._data)
        if stored_in_keyring:
            self._strip_nested(data_to_dump, stored_in_keyring)
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(data_to_dump, f, allow_unicode=True, default_flow_style=False)

    @staticmethod
    def _strip_nested(data: dict, keys):
        """从嵌套字典中删除指定的点分隔键"""
        for key in keys:
            parts = key.split(".")
            d = data
            for part in parts[:-1]:
                if isinstance(d, dict):
                    d = d.get(part, {})
                else:
                    break
            if isinstance(d, dict) and parts[-1] in d:
                del d[parts[-1]]

    @_dualmethod
    def get_all(self) -> dict:
        if not self._data:
            self.load()
        return self._data

    @_dualmethod
    def export_secret_env(self, env: dict[str, str] | None = None, overwrite: bool = False) -> dict[str, str]:
        """Return an env mapping populated with loaded secret values.

        Existing environment values win by default so callers can deliberately
        override keyring/config values for a child process.
        """
        if not self._data:
            self.load()
        result = dict(env or {})
        for secret_key, env_key in _ENV_KEY_MAP.items():
            value = self._get_nested(secret_key)
            if value and (overwrite or not result.get(env_key)):
                result[env_key] = str(value)
        return result

    @_dualmethod
    def get_data_dir(self) -> Path:
        return get_data_dir()

    @_dualmethod
    def get_db_path(self) -> Path:
        return Path(self.get_data_dir()) / str(self.get("storage.db_name", "kb.db"))


