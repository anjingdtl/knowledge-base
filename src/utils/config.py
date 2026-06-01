"""配置管理模块

支持两种使用方式:
    1. 实例模式（DI 推荐）: config = Config(); config.load(); config.get("key")
    2. 类方法模式（兼容旧代码）: Config.load(); Config.get("key")

通过 _dualmethod 描述符实现同一方法名在类/实例上的不同派发。

敏感凭据（api_key）通过 OS keychain（keyring）安全存储，不写入 config.yaml。
"""
import functools
import logging
import yaml
from pathlib import Path
from src.utils.paths import get_config_path, get_data_dir

logger = logging.getLogger(__name__)

# keyring 服务名，用于 OS keychain 中隔离本应用的凭据
_KEYRING_SERVICE = "ShineHeKnowledge"

# 需要通过 keyring 安全存储的配置键（以 api_key 结尾的路径）
_SECRET_KEYS = {
    "llm.api_key",
    "embedding.api_key",
    "reranker.api_key",
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
        cfg = self._data
        for k in keys[:-1]:
            cfg = cfg.setdefault(k, {})
        cfg[keys[-1]] = value

    def _get_nested(self, key: str):
        """内部辅助：直接从 _data 中读取嵌套键"""
        keys = key.split(".")
        val = self._data
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
        # 从 keychain 恢复 API Key
        if _keyring_available:
            for secret_key in _SECRET_KEYS:
                kr_key = _secret_to_keyring_key(secret_key)
                try:
                    secret_val = keyring.get_password(_KEYRING_SERVICE, kr_key)
                    if secret_val is not None:
                        # 写入内存中的 _data，让 get() 能正常读取
                        self._set_nested(secret_key, secret_val)
                except Exception as exc:
                    logger.debug("从 keyring 读取 %s 失败: %s", secret_key, exc)
        # 实例加载时注册为默认
        Config._default_instance = self
        return self._data

    @_dualmethod
    def get(self, key: str, default=None):
        """点号分隔的嵌套键读取，如 'llm.api_key'"""
        if not self._data:
            self.load()
        keys = key.split(".")
        val = self._data
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
                    else:
                        # 值为空则清除 keychain 中对应条目
                        try:
                            keyring.delete_password(_KEYRING_SERVICE, kr_key)
                        except keyring.errors.PasswordDeleteError:
                            pass
                except Exception as exc:
                    logger.warning("写入 keyring %s 失败，将回退到文件存储: %s", secret_key, exc)
        else:
            import copy
            # keyring 不可用时，凭据照旧写入 config.yaml（降级处理）
            pass

        # 写入 YAML 文件时，剥离已通过 keyring 存储的敏感字段
        import copy
        data_to_dump = copy.deepcopy(self._data)
        if _keyring_available:
            self._strip_nested(data_to_dump, (secret_key for secret_key in _SECRET_KEYS))
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
    def get_data_dir(self) -> Path:
        return get_data_dir()

    @_dualmethod
    def get_db_path(self) -> Path:
        return self.get_data_dir() / self.get("storage.db_name", "kb.db")

    @_dualmethod
    def get_chroma_dir(self) -> Path:
        path = self.get_data_dir() / self.get("storage.chroma_dir", "chroma")
        path.mkdir(parents=True, exist_ok=True)
        return path
