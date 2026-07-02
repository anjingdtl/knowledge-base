"""shinehe CLI 单元测试

测试参数解析、子命令分发，不依赖 PySide6。
"""
from unittest.mock import patch

import pytest

from src.cli import main

# ---------------------------------------------------------------------------
# 基础解析测试
# ---------------------------------------------------------------------------


def test_no_command_exits_cleanly(capsys):
    """无子命令时打印帮助并退出 0"""
    with pytest.raises(SystemExit) as exc_info:
        main([])
    assert exc_info.value.code == 0


def test_version_flag(capsys):
    """--version 输出版本信息"""
    with pytest.raises(SystemExit) as exc_info:
        main(["--version"])
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert "ShineHeKnowledge" in captured.out


def test_init_command_parses():
    """init 子命令正确解析参数"""
    with patch("src.cli._handle_init", return_value=0) as mock_init:
        with pytest.raises(SystemExit) as exc_info:
            main(["init", "--local", "--force"])
        assert exc_info.value.code == 0
        mock_init.assert_called_once()
        args = mock_init.call_args[0][0]
        assert args.local is True
        assert args.force is True
        assert args.provider == "siliconflow"


def test_init_command_with_provider():
    """init --provider 正确传递"""
    with patch("src.cli._handle_init", return_value=0):
        with pytest.raises(SystemExit):
            main(["init", "--provider", "openai", "--client", "cursor,cline"])


def test_index_command_parses():
    """index 子命令正确解析参数"""
    with patch("src.cli._handle_index", return_value=0) as mock_index:
        with pytest.raises(SystemExit) as exc_info:
            main(["index", "/tmp/docs", "--recursive", "--dry-run"])
        assert exc_info.value.code == 0
        mock_index.assert_called_once()
        args = mock_index.call_args[0][0]
        assert args.path == "/tmp/docs"
        assert args.recursive is True
        assert args.dry_run is True


def test_watch_command_parses():
    """watch 子命令正确解析参数"""
    with patch("src.cli._handle_watch", return_value=0) as mock_watch:
        with pytest.raises(SystemExit):
            main(["watch", "/tmp/docs", "--recursive"])
        mock_watch.assert_called_once()
        args = mock_watch.call_args[0][0]
        assert args.path == "/tmp/docs"
        assert args.recursive is True


def test_doctor_command_parses():
    """doctor 子命令正确解析参数"""
    with patch("src.cli._handle_doctor", return_value=0) as mock_doctor:
        with pytest.raises(SystemExit):
            main(["doctor", "--config", "/tmp/config.yaml"])
        mock_doctor.assert_called_once()
        args = mock_doctor.call_args[0][0]
        assert args.config == "/tmp/config.yaml"


def test_mcp_command_delegates():
    """mcp 子命令委托给 mcp_cli.main"""
    with patch("src.cli._handle_mcp", return_value=0) as mock_mcp:
        with pytest.raises(SystemExit):
            main(["mcp", "--transport", "stdio"])
        mock_mcp.assert_called_once()
        args = mock_mcp.call_args[0][0]
        assert args.transport == "stdio"


def test_mcp_command_custom_transport():
    """mcp --transport streamable-http 正确解析"""
    with patch("src.cli._handle_mcp", return_value=0):
        with pytest.raises(SystemExit):
            main(["mcp", "--transport", "streamable-http", "--port", "9010"])


# ---------------------------------------------------------------------------
# 分发测试
# ---------------------------------------------------------------------------


def test_handler_return_code_propagates():
    """handler 返回非 0 退出码时正确传播"""
    with patch("src.cli._handle_doctor", return_value=1):
        with pytest.raises(SystemExit) as exc_info:
            main(["doctor"])
        assert exc_info.value.code == 1


def test_handler_warnings_exit_code():
    """handler 返回 2（warnings）时正确传播"""
    with patch("src.cli._handle_doctor", return_value=2):
        with pytest.raises(SystemExit) as exc_info:
            main(["doctor"])
        assert exc_info.value.code == 2


# ---------------------------------------------------------------------------
# wiki-first init e2e 测试
# ---------------------------------------------------------------------------


def test_init_creates_wiki_first_layout(tmp_path):
    """init 命令实际创建 wiki-first 目录契约与 AGENTS.md(e2e,不 mock)"""
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    # 把 config 也写到项目目录,避免污染全局 ~/.shinehe
    with pytest.raises(SystemExit) as exc:
        main(["init", "--local", "--force", "--path", str(project_dir)])
    assert exc.value.code == 0

    # config.yaml
    assert (project_dir / "config.yaml").exists()
    # wiki-first 目录契约
    assert (project_dir / "raw").is_dir()
    assert (project_dir / "wiki" / "sources").is_dir()
    assert (project_dir / "wiki" / "entities").is_dir()
    assert (project_dir / "wiki" / "concepts").is_dir()
    assert (project_dir / "wiki" / "comparisons").is_dir()
    assert (project_dir / "wiki" / "syntheses").is_dir()
    assert (project_dir / "schema").is_dir()
    assert (project_dir / "artifacts" / "eval").is_dir()
    # AGENTS.md
    agents_md = project_dir / "schema" / "AGENTS.md"
    assert agents_md.exists()
    assert "Source of truth" in agents_md.read_text(encoding="utf-8")
