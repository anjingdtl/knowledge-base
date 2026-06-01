"""DSL 查询语言 — 用户友好的文本查询解析

语法:
    {{query (and [[tag]] (property key val) "全文搜索")}}

支持的操作:
    - and, or, not        逻辑组合
    - [[tag]]             标签过滤
    - (property k v)      属性匹配
    - "搜索词"            全文搜索
    - (type xxx)          文件类型过滤
    - (ref id)            引用过滤
    - (limit N)           结果数量限制

用法:
    from src.core.query_dsl import parse_dsl_query, execute_dsl

    ast = parse_dsl_query('(and [[Python]] (property priority high) "async")')
    results = execute_dsl(ast)
"""
import re
import logging
from dataclasses import dataclass
from typing import Union

logger = logging.getLogger(__name__)


# ---- AST 节点 ----

@dataclass
class AndNode:
    children: list

@dataclass
class OrNode:
    children: list

@dataclass
class NotNode:
    child: object

@dataclass
class TagNode:
    tag: str

@dataclass
class PropertyNode:
    key: str
    value: str

@dataclass
class FullTextNode:
    query: str

@dataclass
class TypeNode:
    file_type: str

@dataclass
class RefNode:
    target_id: str

@dataclass
class LimitNode:
    limit: int


# ---- 解析器 ----

class DSLParser:
    """递归下降解析器"""

    def __init__(self, text: str):
        self.text = text.strip()
        self.pos = 0

    def parse(self):
        node = self._parse_expr()
        return node

    def _skip_ws(self):
        while self.pos < len(self.text) and self.text[self.pos] in ' \t\n\r':
            self.pos += 1

    def _parse_expr(self):
        self._skip_ws()
        if self.pos >= len(self.text):
            return None

        # 字符串字面量 → 全文搜索
        if self.text[self.pos] == '"':
            return self._parse_string()

        # 标签 [[tag]]
        if self.text[self.pos:self.pos + 2] == '[[':
            return self._parse_tag()

        # 括号表达式
        if self.text[self.pos] == '(':
            return self._parse_paren()

        # 裸词 → 全文搜索
        return self._parse_bare_word()

    def _parse_string(self):
        self.pos += 1  # skip opening "
        chars = []
        while self.pos < len(self.text):
            ch = self.text[self.pos]
            # 处理转义: \" -> ", \\ -> \
            if ch == '\\' and self.pos + 1 < len(self.text):
                next_ch = self.text[self.pos + 1]
                if next_ch == '"':
                    chars.append('"')
                    self.pos += 2
                    continue
                elif next_ch == '\\':
                    chars.append('\\')
                    self.pos += 2
                    continue
            # 未转义的引号 → 字符串结束
            if ch == '"':
                self.pos += 1
                return FullTextNode(''.join(chars))
            chars.append(ch)
            self.pos += 1
        # 没有找到闭合引号，返回已收集的内容
        return FullTextNode(''.join(chars))

    def _parse_tag(self):
        self.pos += 2  # skip [[
        end = self.text.find(']]', self.pos)
        if end == -1:
            end = len(self.text)
        tag = self.text[self.pos:end]
        self.pos = end + 2
        return TagNode(tag)

    def _parse_paren(self):
        self.pos += 1  # skip (
        self._skip_ws()

        # 读取操作符
        op_start = self.pos
        while self.pos < len(self.text) and self.text[self.pos] not in ' \t\n\r)':
            self.pos += 1
        op = self.text[op_start:self.pos].lower()

        self._skip_ws()

        if op == 'and':
            children = self._parse_children()
            return AndNode(children)
        elif op == 'or':
            children = self._parse_children()
            return OrNode(children)
        elif op == 'not':
            child = self._parse_expr()
            self._skip_ws()
            if self.pos < len(self.text) and self.text[self.pos] == ')':
                self.pos += 1
            return NotNode(child)
        elif op == 'property':
            key, val = self._parse_key_value()
            self._skip_ws()
            if self.pos < len(self.text) and self.text[self.pos] == ')':
                self.pos += 1
            return PropertyNode(key, val)
        elif op == 'type':
            ft = self._parse_word()
            self._skip_ws()
            if self.pos < len(self.text) and self.text[self.pos] == ')':
                self.pos += 1
            return TypeNode(ft)
        elif op == 'ref':
            rid = self._parse_word()
            self._skip_ws()
            if self.pos < len(self.text) and self.text[self.pos] == ')':
                self.pos += 1
            return RefNode(rid)
        elif op == 'limit':
            n = self._parse_word()
            self._skip_ws()
            if self.pos < len(self.text) and self.text[self.pos] == ')':
                self.pos += 1
            try:
                return LimitNode(int(n))
            except (ValueError, TypeError):
                raise ValueError(
                    f"DSL 解析错误: (limit ...) 的参数必须是整数，收到: {n!r}"
                ) from None

        self._skip_ws()
        if self.pos < len(self.text) and self.text[self.pos] == ')':
            self.pos += 1
        return None

    def _parse_children(self):
        children = []
        while self.pos < len(self.text):
            self._skip_ws()
            if self.pos >= len(self.text) or self.text[self.pos] == ')':
                break
            child = self._parse_expr()
            if child:
                children.append(child)
        if self.pos < len(self.text) and self.text[self.pos] == ')':
            self.pos += 1
        return children

    def _parse_key_value(self):
        self._skip_ws()
        key = self._parse_word()
        self._skip_ws()
        val = self._parse_word()
        return key, val

    def _parse_word(self):
        self._skip_ws()
        start = self.pos
        while self.pos < len(self.text) and self.text[self.pos] not in ' \t\n\r)':
            self.pos += 1
        return self.text[start:self.pos]

    def _parse_bare_word(self):
        start = self.pos
        while self.pos < len(self.text) and self.text[self.pos] not in ' \t\n\r()':
            self.pos += 1
        return FullTextNode(self.text[start:self.pos])


def parse_dsl_query(query_text: str):
    """解析 DSL 查询文本为 AST

    支持:
        - 单个 {{query ...}} 块: 提取内部内容
        - 多个 {{query ...}} 块: 用 AND 连接各块的解析结果
        - 裸 DSL (无 {{query}} 外壳): 直接解析
    """
    text = query_text.strip()

    # 查找所有 {{query ...}} 块
    matches = list(re.finditer(r'\{\{query\s+(.*?)\}\}', text, re.DOTALL))

    if not matches:
        # 无外壳，直接解析
        parser = DSLParser(text)
        return parser.parse()

    if len(matches) == 1:
        # 单个块
        inner = matches[0].group(1).strip()
        parser = DSLParser(inner)
        return parser.parse()

    # 多个块 → AND 连接
    children = []
    for m in matches:
        inner = m.group(1).strip()
        if inner:
            parser = DSLParser(inner)
            node = parser.parse()
            if node:
                children.append(node)
    return AndNode(children) if children else None


def execute_dsl(ast, db=None):
    """执行 DSL AST，返回查询结果"""
    from src.core.query_builder import (
        query, has_tag, property as prop, fulltext,
        has_ref_to, file_type,
    )

    if ast is None:
        return []

    # OR 节点需要分别执行每个分支后合并去重
    if isinstance(ast, OrNode):
        logger.info("Executing OR query: %d branches", len(ast.children))
        seen_ids = set()
        all_results = []
        for child in ast.children:
            branch = execute_dsl(child, db=db)
            for r in branch:
                rid = r.get("id")
                if rid and rid not in seen_ids:
                    seen_ids.add(rid)
                    all_results.append(r)
        return all_results

    clauses = _ast_to_clauses(ast)
    limit = 100
    limit_nodes = [c for c in clauses if isinstance(c, LimitNode)]
    if limit_nodes:
        limit = limit_nodes[0].limit
    clauses = [c for c in clauses if not isinstance(c, LimitNode)]

    if not clauses:
        return []

    return query(*clauses, limit=limit, db=db)


def _ast_to_clauses(node) -> list:
    """将 AST 节点转为查询构建器子句列表"""
    if node is None:
        return []

    if isinstance(node, TagNode):
        return [has_tag(node.tag)]
    elif isinstance(node, FullTextNode):
        return [fulltext(node.query)]
    elif isinstance(node, PropertyNode):
        return [prop(node.key, node.value)]
    elif isinstance(node, TypeNode):
        return [file_type(node.file_type)]
    elif isinstance(node, RefNode):
        return [has_ref_to(node.target_id)]
    elif isinstance(node, LimitNode):
        return [node]
    elif isinstance(node, AndNode):
        result = []
        for child in node.children:
            result.extend(_ast_to_clauses(child))
        return result
    elif isinstance(node, OrNode):
        # OR 由 execute_dsl 处理，此处不应被调用
        raise ValueError("OrNode should be handled by execute_dsl, not _ast_to_clauses")
    elif isinstance(node, NotNode):
        logger.warning("NOT queries are not yet supported, ignoring clause")

    return []
