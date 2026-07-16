"""Vector/DB path resolution must honor absolute storage.data_dir under any SHINEHE_HOME."""
from __future__ import annotations

import os
from pathlib import Path

import yaml

from src.utils.paths import resolve_storage_paths, resolve_vector_storage_path


def test_absolute_data_dir_not_overridden_by_shinehe_home(tmp_path: Path) -> None:
    formal_data = tmp_path / "formal_data"
    formal_data.mkdir()
    (formal_data / "kb.db").write_bytes(b"x")
    home = tmp_path / "temp_home"
    home.mkdir()
    cfg_path = home / "config.yaml"
    cfg_path.write_text(
        yaml.dump(
            {
                "storage": {
                    "data_dir": str(formal_data.resolve()),
                    "db_name": "kb.db",
                    "graph_dir": "graph",
                }
            }
        ),
        encoding="utf-8",
    )
    old = os.environ.get("SHINEHE_HOME")
    try:
        os.environ["SHINEHE_HOME"] = str(home)
        paths = resolve_storage_paths(
            config_path=cfg_path,
            shinehe_home=home,
            storage_data_dir=str(formal_data.resolve()),
            db_name="kb.db",
        )
        assert paths["data_dir"] == formal_data.resolve()
        assert paths["db_path"] == (formal_data / "kb.db").resolve()
        assert paths["db_path"] != (home / "data" / "kb.db").resolve()

        vpath = resolve_vector_storage_path(
            config_path=cfg_path,
            shinehe_home=home,
            storage_data_dir=str(formal_data.resolve()),
            vector_backend="sqlite-vec",
        )
        assert vpath == formal_data.resolve()
    finally:
        if old is None:
            os.environ.pop("SHINEHE_HOME", None)
        else:
            os.environ["SHINEHE_HOME"] = old


def test_relative_data_dir_resolves_under_shinehe_home(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / "data").mkdir(parents=True)
    paths = resolve_storage_paths(
        config_path=home / "config.yaml",
        shinehe_home=home,
        storage_data_dir="data",
        db_name="kb.db",
    )
    assert paths["data_dir"] == (home / "data").resolve()
    assert paths["db_path"] == (home / "data" / "kb.db").resolve()
