"""SettingsDialog 中 MCP 配置档卡片的测试。

覆盖两层:
1. PROFILE_INFO 元数据字典(纯数据,无需 Qt)
2. SettingsDialog 实例化 + load/save MCP 字段(用 offscreen Qt)
"""
from __future__ import annotations

import os
import sys

import pytest

from src.mcp.tool_profiles import PROFILE_INFO, PROFILES

# ---------- 元数据契约 ----------

class TestProfileInfoMetadata:
    """PROFILE_INFO 字典契约 — GUI 与文档共享的档位说明。"""

    REQUIRED_KEYS = {"label", "summary", "scope", "use_case", "writes"}

    def test_covers_all_known_profiles(self):
        assert set(PROFILE_INFO.keys()) == PROFILES, (
            f"PROFILE_INFO key 应覆盖全部档位,缺/多: {set(PROFILE_INFO.keys()) ^ PROFILES}"
        )

    def test_each_profile_has_required_fields(self):
        for key, info in PROFILE_INFO.items():
            assert isinstance(info, dict), f"{key} 应为 dict"
            missing = self.REQUIRED_KEYS - set(info.keys())
            assert not missing, f"{key} 缺字段: {missing}"
            for field in self.REQUIRED_KEYS:
                val = info[field]
                assert isinstance(val, str) and val.strip(), (
                    f"{key}.{field} 应为非空字符串,实际: {val!r}"
                )

    def test_label_mentions_profile_name(self):
        """每个 label 都应该包含档位名,便于在 ComboBox 中识别。"""
        for key, info in PROFILE_INFO.items():
            assert key in info["label"].lower(), (
                f"{key} 的 label 应包含档位名:{info['label']!r}"
            )

    def test_extended_is_marked_as_default(self):
        """extended 档应标识为默认/推荐(与新默认值保持一致)。"""
        label = PROFILE_INFO["extended"]["label"]
        assert "默认" in label or "推荐" in label, (
            f"extended 的 label 应标注为默认或推荐:{label!r}"
        )


# ---------- SettingsDialog UI smoke test ----------

@pytest.fixture(scope="module")
def qapp():
    """提供 offscreen QApplication 给 PySide6 控件测试。"""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        pytest.skip("PySide6 未安装")
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture
def settings_dialog(qapp, tmp_path, monkeypatch):
    """实例化 SettingsDialog,Config 替换为隔离的临时实例。"""
    from src.utils.config import Config

    # 用新 Config 实例替换全局默认,避免污染真实环境
    isolated = Config.__new__(Config)
    isolated._data = {
        "mcp": {
            "tool_profile": "extended",
            "enable_legacy_aliases": False,
            "experimental_tools_enabled": False,
        },
        "llm": {"provider": "openai", "base_url": "https://api.openai.com/v1"},
        "appearance": {"theme": "dark", "font_size": 14},
        "graph_backend": {"provider": "sqlite"},
        "wiki": {"enabled": False},
    }
    monkeypatch.setattr(Config, "_default_instance", isolated)

    from src.gui.settings_dialog import SettingsDialog
    dialog = SettingsDialog()
    yield dialog
    dialog.deleteLater()


class TestMcpTabPresent:
    def test_mcp_combo_has_five_profiles(self, settings_dialog):
        combo = settings_dialog.mcp_profile_combo
        assert combo.count() == 5, f"应有 5 个档位选项,实际 {combo.count()}"
        keys = {combo.itemData(i) for i in range(combo.count())}
        assert keys == PROFILES, f"档位 key 不匹配 PROFILES:{keys}"

    def test_load_reflects_existing_config(self, settings_dialog):
        # fixture 中预设了 tool_profile = "extended"
        assert settings_dialog.mcp_profile_combo.currentData() == "extended"

    def test_detail_updates_on_combo_change(self, settings_dialog):
        combo = settings_dialog.mcp_profile_combo
        # 切到 core
        core_idx = combo.findData("core")
        combo.setCurrentIndex(core_idx)
        assert PROFILE_INFO["core"]["summary"] in settings_dialog._mcp_summary_label.text()
        # 切到 legacy
        legacy_idx = combo.findData("legacy")
        combo.setCurrentIndex(legacy_idx)
        assert PROFILE_INFO["legacy"]["summary"] in settings_dialog._mcp_summary_label.text()

    def test_aux_switches_exist(self, settings_dialog):
        # 三个辅助开关存在且默认未勾选
        assert settings_dialog.mcp_enable_aliases is not None
        assert settings_dialog.mcp_enable_experimental is not None
        assert settings_dialog.mcp_enable_wiki is not None
        assert settings_dialog.mcp_enable_aliases.isChecked() is False
        assert settings_dialog.mcp_enable_experimental.isChecked() is False
        assert settings_dialog.mcp_enable_wiki.isChecked() is False


class TestWikiSwitch:
    """wiki.enabled 总开关 —— 此前无任何 UI 可启用它,导致体检/死链修复恒报未启用。"""

    def test_wiki_switch_reflects_config(self, settings_dialog):
        # fixture 中 wiki.enabled = False
        assert settings_dialog.mcp_enable_wiki.isChecked() is False

    def test_wiki_switch_persists_on_save(self, settings_dialog, monkeypatch):
        from src.gui import settings_dialog as sd_mod
        from src.gui import theme as theme_mod
        from src.utils.config import Config

        # 屏蔽真实写盘、主题重应用、弹窗、关闭对话框等副作用
        monkeypatch.setattr(Config, "save", lambda *a, **kw: None)
        monkeypatch.setattr(theme_mod, "apply", lambda *a, **kw: None)
        monkeypatch.setattr(sd_mod.QMessageBox, "information", lambda *a, **kw: None)
        monkeypatch.setattr(sd_mod.QMessageBox, "warning", lambda *a, **kw: None)
        closed = []
        monkeypatch.setattr(settings_dialog, "accept", lambda *a, **kw: closed.append(True))

        settings_dialog.mcp_enable_wiki.setChecked(True)
        settings_dialog._save()

        # wiki.enabled 应被持久化到 Config(操作 _default_instance = isolated)
        assert Config.get("wiki.enabled", False) is True
        assert closed == [], "保存设置不应关闭设置窗口"


class TestConnectionTests:
    def test_connection_test_controls_exist(self, settings_dialog):
        assert settings_dialog.llm_test_button.text() == "测试 LLM 连接"
        assert settings_dialog.embedding_test_button.text() == "测试 Embedding 连接"
        assert settings_dialog.rag_test_button.text() == "测试 RAG 向量连接"
        assert settings_dialog.rerank_test_button.text() == "测试重排序连接"
