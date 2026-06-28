"""Critical bug 修复端到端回归测试

回归 2026-06 代码审查发现的两个 Critical 问题：
- C1: auto_tag 工具用 `db.conn`，而 Database 类无此属性 → 真实执行抛 AttributeError
- C2: tag_inference.infer_tags_by_llm 用 `llm.chat(prompt, max_tokens=, temperature=)`
       → 抛 TypeError（关键字不存在）且字符串 prompt 类型不符

二者都曾被 test_50round_bugfix.py 标记为"通过"，但该测试用 MagicMock 绕过了
真实 DB 访问和真实 LLMService 签名，代码实际从未跑通。本文件用**真实临时 DB**
（conftest autouse setup_db）+ MagicMock LLM，验证完整链路在真实依赖下可执行。
"""
from unittest.mock import MagicMock, patch

from src.services.db import Database
from src.services.tag_inference import infer_tags, infer_tags_by_llm

# ── 测试辅助 ──

def _insert_knowledge(title: str, content: str = "内容", tags=None) -> str:
    """插入一条 knowledge_items 记录，返回 id。tags=None 表示无标签。"""
    import json
    import uuid
    from datetime import datetime

    kid = str(uuid.uuid4())
    now = datetime.now().isoformat()
    tags_json = json.dumps(tags or [], ensure_ascii=False)
    with Database._instance.get_conn() as conn:
        conn.execute(
            "INSERT INTO knowledge_items "
            "(id, title, content, source_type, source_path, file_type, file_size, "
            " content_hash, tags, version, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (kid, title, content, "manual", "", "txt", 0, "", tags_json, 1, now, now),
        )
    return kid


def _get_tags(kid: str) -> list:
    """从真实 DB 查回某条目当前的 tags 字段（解析为 list）。"""
    import json
    with Database._instance.get_conn() as conn:
        row = conn.execute(
            "SELECT tags FROM knowledge_items WHERE id = ?", (kid,)
        ).fetchone()
    if not row or not row["tags"]:
        return []
    try:
        return json.loads(row["tags"])
    except (json.JSONDecodeError, TypeError):
        return []


# ──────────────────────────────────────────────────────────────────
# C1: auto_tag 在真实 DB 下可执行（修复前在 db.conn 处抛 AttributeError）
# ──────────────────────────────────────────────────────────────────


class TestAutoTagRealDb:
    """验证 auto_tag 工具走真实 Database 实例，DB 访问不再崩溃且真实落盘。

    修复前：auto_tag 内 `db.conn.execute(...)` 因 Database 无 conn 属性抛
    AttributeError，被外层 try/except 捕获返回 INTERNAL_ERROR。
    """

    def _patch_container(self, mock_llm):
        """注入只含 .llm 的 mock container；auto_tag 内部 db = Database._instance
        走真实 DB（setup_db 已建好）。"""
        import src.mcp_server as mcp_mod

        mock_container = MagicMock()
        mock_container.llm = mock_llm
        original_get = mcp_mod._get_container
        original_check = mcp_mod._check_write_policy
        mcp_mod._get_container = lambda: mock_container
        mcp_mod._check_write_policy = lambda *a, **kw: None
        return original_get, original_check

    def _restore(self, original_get, original_check):
        import src.mcp_server as mcp_mod
        mcp_mod._get_container = original_get
        mcp_mod._check_write_policy = original_check

    def test_auto_tag_writes_tags_with_real_db(self):
        """C1 核心：auto_tag 在真实 DB 上能成功打标并落盘。"""
        import src.mcp_server as mcp_mod
        from tests.conftest import insert_test_knowledge

        kid1 = insert_test_knowledge("采购管理办法", "采购流程说明", tags=None)
        kid2 = insert_test_knowledge("安全管理制度", "信息安全规范", tags=None)

        mock_llm = MagicMock()
        mock_llm.chat_with_usage.return_value = ('["采购管理"]', {})

        original_get, original_check = self._patch_container(mock_llm)
        try:
            result = mcp_mod.auto_tag(limit=5)
        finally:
            self._restore(original_get, original_check)

        assert result["ok"] is True, f"auto_tag 应成功，实际: {result}"
        assert result["data"]["tagged_count"] == 2, \
            f"应打标 2 条无标签条目，实际: {result['data']['tagged_count']}"

        # 关键：从真实 DB 查回，验证标签真实落盘（非 mock）
        assert _get_tags(kid1) == ["采购管理"], \
            f"kid1 标签应落盘为 ['采购管理']，实际: {_get_tags(kid1)}"
        assert _get_tags(kid2) == ["采购管理"], \
            f"kid2 标签应落盘为 ['采购管理']，实际: {_get_tags(kid2)}"

    def test_auto_tag_skips_already_tagged(self):
        """边界：force=False 时跳过已有标签的条目，只处理无标签的。"""
        import src.mcp_server as mcp_mod
        from tests.conftest import insert_test_knowledge

        # 已有标签 → 应被跳过
        insert_test_knowledge("已打标文档", "内容", tags=["已有标签"])
        # 无标签 → 应被打标
        kid_untagged = insert_test_knowledge("待打标文档", "内容", tags=None)

        mock_llm = MagicMock()
        mock_llm.chat_with_usage.return_value = ('["新标签"]', {})

        original_get, original_check = self._patch_container(mock_llm)
        try:
            result = mcp_mod.auto_tag(limit=5, force=False)
        finally:
            self._restore(original_get, original_check)

        assert result["ok"] is True
        assert result["data"]["tagged_count"] == 1, \
            f"force=False 应只打标 1 条无标签条目，实际: {result['data']['tagged_count']}"
        assert _get_tags(kid_untagged) == ["新标签"]

    def test_auto_tag_bad_llm_json_skips_row_without_internal_error(self):
        """LLM 返回坏 JSON 时应记录单条错误，而不是因为 sqlite3.Row.get 再次崩溃。"""
        import src.mcp_server as mcp_mod
        from tests.conftest import insert_test_knowledge

        kid = insert_test_knowledge("坏响应文档", "内容", tags=None)

        mock_llm = MagicMock()
        mock_llm.chat_with_usage.return_value = ("不是 JSON", {})

        original_get, original_check = self._patch_container(mock_llm)
        try:
            result = mcp_mod.auto_tag(limit=1)
        finally:
            self._restore(original_get, original_check)

        assert result["ok"] is True, f"auto_tag 应返回部分成功结构，实际: {result}"
        assert result["data"]["tagged_count"] == 0
        assert result["data"]["skipped_count"] == 1
        assert result["meta"]["error_count"] == 1
        assert _get_tags(kid) == []


# ──────────────────────────────────────────────────────────────────
# C2: infer_tags_by_llm 调用 LLM 的签名正确（修复前抛 TypeError）
# ──────────────────────────────────────────────────────────────────


class TestInferTagsByLlmSignature:
    """验证 infer_tags_by_llm 用正确的 LLMService.chat 签名调用。

    修复前：`llm.chat(prompt, max_tokens=300, temperature=0.1)`
    - max_tokens/temperature 不是 chat() 的合法关键字 → TypeError
    - 第一参数传字符串而非 messages list → 类型不符
    TypeError 被 except (json.JSONDecodeError, TypeError, ValueError) 捕获返回 []，
    导致 LLM 兜底分支永远静默失败。

    注意：infer_tags_by_llm 内部自己 `new LLMService()`（不接受外部注入），
    因此用 patch 替换 LLMService 类来测真实调用路径。
    """

    def test_infer_tags_by_llm_uses_correct_signature(self):
        """C2 核心：infer_tags_by_llm 应用 messages list 调用 chat，返回解析结果。"""
        mock_llm = MagicMock()
        mock_llm.chat.return_value = '[{"tag": "测试", "confidence": 0.9}]'

        # tag_inference 内部局部 import LLMService，故 patch 源模块
        with patch("src.services.llm.LLMService", return_value=mock_llm):
            results = infer_tags_by_llm("测试标题", "测试内容", [])

        # 修复前 TypeError 被吞，results 为 []
        assert len(results) == 1, f"应返回 1 个标签，实际: {results}"
        assert results[0]["tag"] == "测试"

        # 验证调用签名
        assert mock_llm.chat.called, "应调用 llm.chat"
        call_args = mock_llm.chat.call_args
        first_arg = call_args.args[0] if call_args.args else call_args[0][0]
        assert isinstance(first_arg, list), \
            f"chat 第一参数应为 messages list，实际类型: {type(first_arg)}"
        assert first_arg[0]["role"] == "user"
        assert "测试标题" in first_arg[0]["content"]

        # 关键：不应出现非法关键字
        kwargs = call_args.kwargs
        assert "max_tokens" not in kwargs, \
            "不应使用 max_tokens（应改用 max_tokens_override）"
        assert "temperature" not in kwargs, \
            "不应使用 temperature（chat 不支持，由 config 统一控制）"

    def test_infer_tags_integration_with_llm_fallback(self):
        """集成：规则推理不足时（len(results) < 2），infer_tags(use_llm=True)
        触发 LLM 分支。

        infer_tags 进入 LLM 的条件是 `use_llm and len(results) < 2`。
        需构造一个标题/路径不命中正则、且内容足够短（<20 字符，让 tfidf 返回空）
        的条目，使规则推理返回 0~1 个结果，从而触发 LLM 兜底。
        """
        mock_llm = MagicMock()
        mock_llm.chat.return_value = '[{"tag": "自定义主题", "confidence": 0.85}]'

        # 标题/路径不命中正则；内容 <20 字符使 infer_tags_from_tfidf 返回空
        item = {
            "title": "XYZ123",
            "source_path": "",
            "content": "短文本",
        }
        # tag_inference 内部局部 import LLMService，故 patch 源模块
        with patch("src.services.llm.LLMService", return_value=mock_llm):
            results = infer_tags(item, vocab=[], use_llm=True)

        # 应包含 LLM 来源的标签
        llm_tags = [r for r in results if r.get("source") == "llm"]
        assert len(llm_tags) >= 1, \
            f"规则推理不足时应触发 LLM 分支，实际 results: {results}"
        assert llm_tags[0]["tag"] == "自定义主题"
