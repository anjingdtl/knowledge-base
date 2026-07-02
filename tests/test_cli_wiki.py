"""shinehe wiki 子命令测试。"""
from unittest.mock import patch

import pytest

from src.cli import main


def test_wiki_lint_command_parses():
    with patch("src.cli._handle_wiki", return_value=0) as mock:
        with pytest.raises(SystemExit) as exc:
            main(["wiki", "lint"])
        assert exc.value.code == 0
        mock.assert_called_once()
        args = mock.call_args[0][0]
        assert args.wiki_command == "lint"


def test_wiki_save_answer_command_parses():
    with patch("src.cli._handle_wiki", return_value=0) as mock:
        with pytest.raises(SystemExit):
            main(["wiki", "save-answer", "--question", "Q?", "--answer", "A"])
        args = mock.call_args[0][0]
        assert args.wiki_command == "save-answer"
        assert args.question == "Q?"
        assert args.answer == "A"


def test_wiki_ingest_source_command_parses():
    with patch("src.cli._handle_wiki", return_value=0) as mock:
        with pytest.raises(SystemExit):
            main(["wiki", "ingest-source", "/path/to/file.md"])
        args = mock.call_args[0][0]
        assert args.wiki_command == "ingest-source"
        assert args.path == "/path/to/file.md"


def test_wiki_no_subcommand_prints_help(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["wiki"])
    assert exc.value.code == 0
