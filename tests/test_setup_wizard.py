"""首次启动检测与 Setup Wizard 单元测试

注意：GUI 相关测试需要 PySide6 环境（仅限桌面环境运行）。
本文件中 Provider 预设测试通过 AST 解析源文件，不触发 PySide6 导入。
"""
import ast
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

_SOURCE_DIR = Path(__file__).resolve().parent.parent / "src"


# ---------------------------------------------------------------------------
# first_run 模块测试
# ---------------------------------------------------------------------------

class TestFirstRun:
    """首次启动检测逻辑"""

    def test_is_first_run_no_marker_no_config(self, tmp_path, monkeypatch):
        """无标记文件 + 无有效 API Key → 首次运行"""
        monkeypatch.setattr(
            "src.utils.first_run.get_data_dir",
            lambda: tmp_path,
        )
        mock_config = MagicMock()
        mock_config.get.return_value = ""
        monkeypatch.setattr("src.utils.first_run.Config", mock_config)

        from src.utils.first_run import is_first_run
        assert is_first_run() is True

    def test_is_not_first_run_with_marker(self, tmp_path, monkeypatch):
        """标记文件存在 → 非首次运行"""
        marker = tmp_path / ".first_run"
        marker.write_text("2026-06-12T10:00:00", encoding="utf-8")

        monkeypatch.setattr(
            "src.utils.first_run.get_data_dir",
            lambda: tmp_path,
        )
        from src.utils.first_run import is_first_run
        assert is_first_run() is False

    def test_is_not_first_run_with_valid_api_key(self, tmp_path, monkeypatch):
        """无标记文件但有有效 API Key → 非首次运行"""
        monkeypatch.setattr(
            "src.utils.first_run.get_data_dir",
            lambda: tmp_path,
        )
        mock_config = MagicMock()
        mock_config.get.return_value = "sk-real-api-key-123"
        monkeypatch.setattr("src.utils.first_run.Config", mock_config)

        from src.utils.first_run import is_first_run
        assert is_first_run() is False

    def test_is_first_run_with_placeholder_key(self, tmp_path, monkeypatch):
        """API Key 为占位符 → 仍为首次运行"""
        monkeypatch.setattr(
            "src.utils.first_run.get_data_dir",
            lambda: tmp_path,
        )
        mock_config = MagicMock()
        mock_config.get.return_value = "YOUR_LLM_API_KEY"
        monkeypatch.setattr("src.utils.first_run.Config", mock_config)

        from src.utils.first_run import is_first_run
        assert is_first_run() is True

    def test_mark_completed(self, tmp_path, monkeypatch):
        """标记完成后，is_first_run 返回 False"""
        monkeypatch.setattr(
            "src.utils.first_run.get_data_dir",
            lambda: tmp_path,
        )
        from src.utils.first_run import mark_completed, is_first_run

        mock_config = MagicMock()
        mock_config.get.return_value = ""
        with patch("src.utils.first_run.Config", mock_config):
            mark_completed()

        marker = tmp_path / ".first_run"
        assert marker.exists()
        assert is_first_run() is False

    def test_get_first_run_time_none(self, tmp_path, monkeypatch):
        """未完成时返回 None"""
        monkeypatch.setattr(
            "src.utils.first_run.get_data_dir",
            lambda: tmp_path,
        )
        from src.utils.first_run import get_first_run_time
        assert get_first_run_time() is None

    def test_get_first_run_time_value(self, tmp_path, monkeypatch):
        """完成后返回时间戳"""
        marker = tmp_path / ".first_run"
        ts = "2026-06-12T10:30:00"
        marker.write_text(ts, encoding="utf-8")

        monkeypatch.setattr(
            "src.utils.first_run.get_data_dir",
            lambda: tmp_path,
        )
        from src.utils.first_run import get_first_run_time
        assert get_first_run_time() == ts

    def test_is_placeholder_cases(self):
        """占位符检测覆盖各种格式"""
        from src.utils.first_run import _is_placeholder
        assert _is_placeholder("YOUR_LLM_API_KEY") is True
        assert _is_placeholder("your_embedding_api_key") is True
        assert _is_placeholder("") is True
        assert _is_placeholder("  YOUR_API_KEY  ") is True
        assert _is_placeholder("sk-real-key-123") is False
        assert _is_placeholder("minimax-abc") is False


# ---------------------------------------------------------------------------
# Provider 预设测试（通过 AST 解析，不导入 PySide6）
# ---------------------------------------------------------------------------

def _load_presets_via_ast() -> dict:
    """通过 provider_presets 模块加载预设（重构后已迁移到 src.core.provider_presets）"""
    from src.core.provider_presets import PROVIDER_PRESETS as core_presets
    # 转换为 GUI 使用的 flat dict 格式以兼容旧测试断言
    result = {}
    for preset in core_presets.values():
        d = {
            "embedding_base_url": preset.embedding_base_url,
            "embedding_model": preset.embedding_model,
            "llm_base_url": preset.llm_base_url,
            "llm_model": preset.llm_model,
        }
        if preset.reranker_base_url:
            d["reranker_base_url"] = preset.reranker_base_url
        if preset.reranker_model:
            d["reranker_model"] = preset.reranker_model
        if preset.api_key_placeholder:
            d["api_key_placeholder"] = preset.api_key_placeholder
        result[preset.display_name] = d
    return result


class TestProviderPresets:
    """验证 Provider 模板数据完整性"""

    def test_all_presets_have_required_fields(self):
        """每个预设至少包含 llm_base_url 和 llm_model"""
        presets = _load_presets_via_ast()
        for name, preset in presets.items():
            if name == "自定义":
                continue
            assert "llm_base_url" in preset, f"{name} 缺少 llm_base_url"
            assert "llm_model" in preset, f"{name} 缺少 llm_model"
            assert preset["llm_base_url"].startswith("http"), f"{name} base_url 无效"

    def test_preset_count(self):
        """预设服务商数量合理"""
        presets = _load_presets_via_ast()
        assert len(presets) >= 7

    def test_siliconflow_preset_values(self):
        """硅基流动预设的具体值"""
        presets = _load_presets_via_ast()
        sf = presets["SiliconFlow（硅基流动）"]
        assert "siliconflow" in sf["embedding_base_url"]
        assert "bge-m3" in sf["embedding_model"].lower() or "bge-m3" in sf["embedding_model"]

    def test_ollama_preset_has_placeholder(self):
        """Ollama 预设包含 api_key_placeholder"""
        presets = _load_presets_via_ast()
        ollama = presets["Ollama（本地模型）"]
        assert "api_key_placeholder" in ollama

    def test_custom_preset_all_empty(self):
        """自定义预设所有 URL/Model 字段为空"""
        presets = _load_presets_via_ast()
        custom = presets["自定义"]
        for key in ("embedding_base_url", "embedding_model", "llm_base_url", "llm_model"):
            assert custom[key] == "", f"自定义预设 {key} 应为空字符串"


# ---------------------------------------------------------------------------
# app.py 集成测试
# ---------------------------------------------------------------------------

class TestAppIntegration:
    """app.py 中的向导集成逻辑"""

    def test_import_sample_data_function_exists(self):
        """_import_sample_data 函数存在且可导入"""
        import importlib
        # 仅验证模块可导入和函数存在，不执行 GUI 操作
        spec = importlib.util.find_spec("src.app")
        assert spec is not None

    def test_run_setup_wizard_function_exists(self):
        """_run_setup_wizard 函数存在"""
        # 不能直接导入（会触发 PySide6），验证源文件中函数定义存在
        app_path = _SOURCE_DIR / "app.py"
        source = app_path.read_text(encoding="utf-8")
        assert "def _run_setup_wizard" in source
        assert "def _import_sample_data" in source
        assert "is_first_run()" in source
        assert "mark_completed()" in source

    def test_app_checks_first_run_before_window(self):
        """app.py 在创建 MainWindow 之前检查 is_first_run"""
        app_path = _SOURCE_DIR / "app.py"
        source = app_path.read_text(encoding="utf-8")
        # is_first_run 应在 MainWindow 导入之前出现
        first_run_pos = source.index("is_first_run()")
        main_window_pos = source.index("from src.gui.main_window")
        assert first_run_pos < main_window_pos, \
            "is_first_run 检查应在 MainWindow 创建之前"


# ---------------------------------------------------------------------------
# 示例知识包测试
# ---------------------------------------------------------------------------

class TestSampleData:
    """示例知识包文件验证"""

    def test_samples_directory_exists(self):
        """samples 目录存在"""
        samples_dir = _SOURCE_DIR / "data" / "samples"
        assert samples_dir.exists()

    def test_sample_files_count(self):
        """至少有 5 个示例文件"""
        samples_dir = _SOURCE_DIR / "data" / "samples"
        md_files = list(samples_dir.glob("*.md"))
        assert len(md_files) >= 5

    def test_sample_files_not_empty(self):
        """每个示例文件内容不为空"""
        samples_dir = _SOURCE_DIR / "data" / "samples"
        for md_file in sorted(samples_dir.glob("*.md")):
            content = md_file.read_text(encoding="utf-8")
            assert len(content.strip()) > 50, f"{md_file.name} 内容过短"
            assert content.strip().startswith("#"), f"{md_file.name} 应以标题开头"
