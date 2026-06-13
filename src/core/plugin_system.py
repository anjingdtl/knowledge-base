"""插件系统 — 生命周期管理 + 事件总线集成

用法:
    # 插件清单 plugin.yaml
    name: my-plugin
    version: 1.0.0
    description: 自定义插件
    hooks:
      - knowledge.created
      - rag.before_search
    provides:
      - custom_stage: my_plugin.MyRAGStage
    settings:
      - key: api_endpoint
        type: string
        default: ""

    # 加载插件
    pm = PluginManager()
    pm.discover("plugins/")
    pm.initialize_all(container)
"""
import importlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)


@dataclass
class PluginManifest:
    """插件清单"""
    name: str
    version: str = "0.0.0"
    description: str = ""
    hooks: list[str] = field(default_factory=list)
    provides: list[str] = field(default_factory=list)
    settings: list[dict] = field(default_factory=list)
    module: str = ""  # Python 模块路径
    _instance: Any = None
    _source_dir: Optional[Path] = None  # plugin.yaml 所在目录


class PluginInterface:
    """插件基类 — 可选继承，提供标准生命周期"""

    def on_load(self, container):
        """插件加载时调用"""
        pass

    def on_unload(self):
        """插件卸载时调用"""
        pass


class PluginManager:
    """插件管理器 — 发现、加载、生命周期管理"""

    def __init__(self):
        self._plugins: dict[str, PluginManifest] = {}
        self._loaded: dict[str, Any] = {}  # name → plugin instance

    def discover(self, plugin_dir: str | Path):
        """扫描目录下的 plugin.yaml 文件"""
        plugin_dir = Path(plugin_dir)
        if not plugin_dir.exists():
            logger.info("Plugin directory not found: %s", plugin_dir)
            return

        for yaml_file in plugin_dir.rglob("plugin.yaml"):
            try:
                with open(yaml_file, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}

                # 类型校验
                if not isinstance(data, dict):
                    raise ValueError(f"plugin.yaml must be a mapping, got {type(data).__name__}")
                for field_name, expected_type in [("hooks", list), ("provides", list), ("settings", list)]:
                    value = data.get(field_name, [])
                    if not isinstance(value, expected_type):
                        raise ValueError(f"'{field_name}' must be a list, got {type(value).__name__}")
                if not isinstance(data.get("settings", []), list):
                    raise ValueError("'settings' must be a list")
                for i, item in enumerate(data.get("settings", [])):
                    if not isinstance(item, dict):
                        raise ValueError(f"settings[{i}] must be a dict, got {type(item).__name__}")
                name_val = data.get("name", yaml_file.parent.name)
                if not isinstance(name_val, str) or not name_val.strip():
                    raise ValueError("'name' must be a non-empty string")
                for str_field in ("version", "description", "module"):
                    val = data.get(str_field, "")
                    if not isinstance(val, str):
                        raise ValueError(f"'{str_field}' must be a string, got {type(val).__name__}")

                manifest = PluginManifest(
                    name=data.get("name", yaml_file.parent.name),
                    version=data.get("version", "0.0.0"),
                    description=data.get("description", ""),
                    hooks=data.get("hooks", []),
                    provides=data.get("provides", []),
                    settings=data.get("settings", []),
                    module=data.get("module", ""),
                    _source_dir=yaml_file.parent.resolve(),
                )
                self._plugins[manifest.name] = manifest
                logger.info("Discovered plugin: %s v%s", manifest.name, manifest.version)
            except Exception as e:
                logger.warning("Failed to load plugin manifest %s: %s", yaml_file, e)

    def register(self, manifest: PluginManifest):
        """手动注册插件"""
        self._plugins[manifest.name] = manifest

    def initialize_all(self, container):
        """初始化所有已发现的插件"""
        for name, manifest in self._plugins.items():
            try:
                self._initialize_one(name, manifest, container)
            except Exception as e:
                logger.error("Failed to initialize plugin %s: %s", name, e)

    def _initialize_one(self, name: str, manifest: PluginManifest, container):
        """初始化单个插件"""
        if not manifest.module:
            logger.debug("Plugin %s has no module, skipping init", name)
            return

        # 安全校验：插件模块文件必须位于插件源目录下
        module_rel_path = manifest.module.replace(".", "/")
        module_file = Path(f"{module_rel_path}.py")
        module_pkg = Path(module_rel_path) / "__init__.py"
        if manifest._source_dir is not None:
            # 自动发现的插件：模块文件必须在插件目录树下
            source_dir = manifest._source_dir
            valid = False
            for candidate in (module_file, module_pkg):
                try:
                    resolved = (source_dir / candidate).resolve()
                    if resolved.is_file() and str(resolved).startswith(str(source_dir)):
                        valid = True
                        break
                except (ValueError, OSError):
                    continue
            if not valid:
                logger.error(
                    "Plugin module %s does not resolve to a file under %s — rejecting for security",
                    manifest.module, source_dir,
                )
                return
        else:
            # 手动注册的插件（无源目录）：仅允许 plugins.* 命名空间
            if not manifest.module.startswith("plugins."):
                logger.error("Manually registered plugin must use plugins.* namespace: %s", manifest.module)
                return

        try:
            mod = importlib.import_module(manifest.module)
        except Exception as e:
            logger.error("Cannot import plugin module %s: %s", manifest.module, e)
            return

        # 查找插件类（优先找 PluginInterface 子类，否则找同名类）
        plugin_cls = None
        for attr_name in dir(mod):
            attr = getattr(mod, attr_name)
            if isinstance(attr, type) and issubclass(attr, PluginInterface) and attr is not PluginInterface:
                plugin_cls = attr
                break
        if not plugin_cls:
            # fallback: 找模块中与插件名匹配的类
            class_name = manifest.name.replace("-", "_").replace(" ", "_").title().replace("_", "")
            plugin_cls = getattr(mod, class_name, None)

        if not plugin_cls:
            logger.warning("No plugin class found in %s", manifest.module)
            return

        instance = plugin_cls()
        self._loaded[name] = instance
        manifest._instance = instance

        # 注册事件钩子
        from src.core.events import on

        for hook_name in manifest.hooks:
            handler = getattr(instance, hook_name.replace(".", "_"), None)
            if handler:
                on(hook_name, handler)
                logger.debug("Plugin %s hooked %s", name, hook_name)

        # 注册自定义 RAG 阶段
        for provision in manifest.provides:
            if ":" in provision:
                stage_name, stage_path = provision.split(":", 1)
                module_path, class_name = stage_path.rsplit(".", 1)
                try:
                    stage_mod = importlib.import_module(module_path)
                    stage_cls = getattr(stage_mod, class_name)
                    from src.services.rag_pipeline import StageRegistry
                    StageRegistry.register(stage_name.strip(), stage_cls)
                    logger.info("Plugin %s registered stage: %s", name, stage_name.strip())
                except Exception as e:
                    logger.error("Failed to register stage %s: %s", stage_name, e)

        # 调用 on_load
        if hasattr(instance, "on_load"):
            instance.on_load(container)

        logger.info("Plugin %s initialized", name)

    def unload_all(self):
        """卸载所有插件"""
        for name, instance in self._loaded.items():
            try:
                if hasattr(instance, "on_unload"):
                    instance.on_unload()
            except Exception as e:
                logger.warning("Error unloading plugin %s: %s", name, e)
        self._loaded.clear()

    def get_plugin(self, name: str) -> Any | None:
        return self._loaded.get(name)

    def list_plugins(self) -> list[dict]:
        return [
            {"name": m.name, "version": m.version, "description": m.description,
             "loaded": m.name in self._loaded}
            for m in self._plugins.values()
        ]
