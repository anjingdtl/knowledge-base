"""Version metadata consistency — FIX-4 (v1.10.2 spec §8).

Asserts the canonical version (``src/version.py``) matches the README
version badge and the client package metadata (``package.json`` and the
``package-lock.json`` root package), so front-end and back-end version
metadata can never silently drift again.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXPECTED_VERSION = "1.10.5"


def _read_version_py() -> str:
    text = (ROOT / "src" / "version.py").read_text(encoding="utf-8")
    match = re.search(r'VERSION\s*=\s*"([^"]+)"', text)
    assert match, "VERSION literal not found in src/version.py"
    return match.group(1)


def _read_package_json_version() -> str:
    data = json.loads((ROOT / "client" / "package.json").read_text(encoding="utf-8"))
    return data["version"]


def _read_lockfile_root_version() -> str:
    data = json.loads(
        (ROOT / "client" / "package-lock.json").read_text(encoding="utf-8")
    )
    top = data.get("version")
    root_pkg = data.get("packages", {}).get("", {}).get("version")
    assert top == root_pkg, (
        "package-lock.json top-level version and packages[''] version diverge: "
        f"{top!r} vs {root_pkg!r}"
    )
    return top


def _read_badge_version(filename: str) -> str:
    text = (ROOT / filename).read_text(encoding="utf-8")
    match = re.search(r"badge/version-([0-9.]+)-blue\.svg", text)
    assert match, f"version badge not found in {filename}"
    return match.group(1)


class TestVersionConsistency:
    def test_src_version_is_expected(self):
        assert _read_version_py() == EXPECTED_VERSION

    def test_readme_badge_matches_src(self):
        assert _read_badge_version("README.md") == _read_version_py()

    def test_readme_zh_badge_matches_src(self):
        assert _read_badge_version("README_zh.md") == _read_version_py()

    def test_package_json_matches_src(self):
        assert _read_package_json_version() == _read_version_py()

    def test_lockfile_root_matches_package_json(self):
        assert _read_lockfile_root_version() == _read_package_json_version()

    def test_lockfile_root_matches_src(self):
        assert _read_lockfile_root_version() == _read_version_py()
