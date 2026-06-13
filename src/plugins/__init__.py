"""可扩展插件系统"""
import importlib
import os
import sys
from pathlib import Path
from typing import Callable


class PluginHook:
    """插件钩子注册表"""
    _hooks: dict[str, list[Callable]] = {}

    @classmethod
    def register(cls, hook_name: str, fn: Callable):
        cls._hooks.setdefault(hook_name, []).append(fn)

    @classmethod
    def fire(cls, hook_name: str, *args, **kwargs):
        results = []
        for fn in cls._hooks.get(hook_name, []):
            results.append(fn(*args, **kwargs))
        return results

    @classmethod
    def list_hooks(cls) -> dict[str, int]:
        return {k: len(v) for k, v in cls._hooks.items()}


class PluginManager:
    """插件管理器 — 动态加载 plugins/ 目录下的模块"""
    _loaded: dict[str, object] = {}

    @classmethod
    def discover(cls, plugin_dir: str | None = None):
        if plugin_dir is None:
            plugin_dir = str(Path(__file__).parent)
        if not os.path.isdir(plugin_dir):
            return
        for fname in os.listdir(plugin_dir):
            if fname.startswith("_") or not fname.endswith(".py"):
                continue
            name = fname[:-3]
            cls.load(name, plugin_dir)

    @classmethod
    def load(cls, name: str, plugin_dir: str | None = None):
        if name in cls._loaded:
            return cls._loaded[name]
        if plugin_dir:
            sys.path.insert(0, plugin_dir)
        try:
            mod = importlib.import_module(name)
            if hasattr(mod, "register"):
                mod.register(PluginHook)
            cls._loaded[name] = mod
            return mod
        except Exception as e:
            print(f"[PLUGIN] 加载插件 {name} 失败: {e}")
            return None

    @classmethod
    def unload(cls, name: str):
        if name in cls._loaded:
            del cls._loaded[name]

    @classmethod
    def list_plugins(cls) -> list[str]:
        return list(cls._loaded.keys())

    @classmethod
    def get_plugin_info(cls) -> list[dict]:
        infos = []
        for name, mod in cls._loaded.items():
            infos.append({
                "name": name,
                "hooks": getattr(mod, "HOOKS", []),
                "version": getattr(mod, "VERSION", "0.1.0"),
                "description": getattr(mod, "__doc__", "").strip(),
            })
        return infos
