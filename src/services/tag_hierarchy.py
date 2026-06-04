"""标签层级服务 — DAG 遍历与环检测"""
from collections import defaultdict, deque

from src.models.tag_relation import TagRelation
from src.repositories.tag_relation_repo import TagRelationRepository
from src.services.db import Database


class TagHierarchyService:
    def __init__(self, db=None, repo=None):
        self._db = db or Database
        self._repo = repo or TagRelationRepository(db=self._db)

    # -- public API ----------------------------------------------------------

    def add_relation(self, parent_tag: str, child_tag: str) -> None:
        """添加父子关系，检测并拒绝环。"""
        if parent_tag == child_tag:
            raise ValueError("self-referential relation is not allowed")

        # 环检测：如果 child_tag 已经是 parent_tag 的后代，加入后形成环
        # children_map: parent -> [child, ...]，沿 child 方向做 BFS
        children = self._children_map()
        visited = set()
        queue = deque(children.get(child_tag, []))
        while queue:
            current = queue.popleft()
            if current == parent_tag:
                raise ValueError(
                    f"cycle detected: adding {parent_tag} -> {child_tag} would create a cycle"
                )
            if current in visited:
                continue
            visited.add(current)
            queue.extend(children.get(current, []))

        self._repo.upsert(TagRelation(parent_tag=parent_tag, child_tag=child_tag))

    def descendants(self, tag: str) -> list[str]:
        """返回 tag 的所有后代标签（不含自身）。"""
        return self._walk(self._children_map(), tag)

    def ancestors(self, tag: str) -> list[str]:
        """返回 tag 的所有祖先标签（不含自身）。"""
        return self._walk(self._parents_map(), tag)

    def expand(self, tag: str, include_self: bool = True) -> list[str]:
        """展开 tag 及其所有后代。"""
        result = self.descendants(tag)
        if include_self:
            result = [tag] + result
        return result

    # -- internal helpers ----------------------------------------------------

    def _walk(self, graph: dict[str, list[str]], root: str) -> list[str]:
        """BFS 遍历，返回按层级顺序排列的节点（不含 root）。"""
        visited = set()
        queue = deque(graph.get(root, []))
        order: list[str] = []
        while queue:
            node = queue.popleft()
            if node in visited:
                continue
            visited.add(node)
            order.append(node)
            queue.extend(graph.get(node, []))
        return order

    def _children_map(self) -> dict[str, list[str]]:
        """parent -> [child, ...] 映射。"""
        mapping: dict[str, list[str]] = defaultdict(list)
        for rel in self._repo.list_all():
            mapping[rel.parent_tag].append(rel.child_tag)
        return dict(mapping)

    def _parents_map(self) -> dict[str, list[str]]:
        """child -> [parent, ...] 映射。"""
        mapping: dict[str, list[str]] = defaultdict(list)
        for rel in self._repo.list_all():
            mapping[rel.child_tag].append(rel.parent_tag)
        return dict(mapping)
