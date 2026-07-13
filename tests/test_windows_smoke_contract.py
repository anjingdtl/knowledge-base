from pathlib import Path

SCRIPT = Path("scripts/windows-smoke.ps1")


def test_windows_smoke_uses_an_isolated_temp_workspace():
    text = SCRIPT.read_text(encoding="utf-8")

    assert "GetTempPath" in text
    assert "SHINEHE_HOME" in text
    assert "Remove-Item -LiteralPath $tempRoot -Recurse -Force" in text
    assert "config.yaml/data/raw/wiki" not in text


def test_windows_smoke_covers_cli_and_real_mcp_lifecycle():
    text = SCRIPT.read_text(encoding="utf-8")

    for expected in (
        "src.cli --help",
        "src.cli init",
        "src.cli index",
        "src.mcp_cli",
        "streamable-http",
        "scripts/check_mcp.py",
        "--smoke-reads",
        "Stop-Process",
    ):
        assert expected in text
