"""示例插件 — 导入后自动为知识条目添加统计信息"""
HOOKS = ["on_knowledge_created", "on_knowledge_deleted"]
VERSION = "1.0.0"


def register(hook_registry):
    hook_registry.register("on_knowledge_created", on_created)
    hook_registry.register("on_knowledge_deleted", on_deleted)


def on_created(knowledge_id: str, **kwargs):
    print(f"[PLUGIN] 知识条目已创建: {knowledge_id}")
    return {"status": "logged"}


def on_deleted(knowledge_id: str, **kwargs):
    print(f"[PLUGIN] 知识条目已删除: {knowledge_id}")
    return {"status": "logged"}
