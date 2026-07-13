from __future__ import annotations

from src.services.verified_hybrid_config_migrator import VerifiedHybridConfigMigrator


def test_config_migration_dry_run_apply_and_byte_rollback(tmp_path):
    path = tmp_path / "config.yaml"
    original = "knowledge_workflow:\n  mode: wiki_first\ncustom:\n  keep: exact\n"
    path.write_text(original, encoding="utf-8")
    original_bytes = path.read_bytes()
    migrator = VerifiedHybridConfigMigrator(path)

    preview = migrator.dry_run()
    assert preview.dry_run is True
    assert path.read_text(encoding="utf-8") == original

    applied = migrator.apply()
    assert applied.backup_path
    changed = path.read_text(encoding="utf-8")
    assert "mode: authoring" in changed
    assert "keep: exact" in changed

    migrator.rollback(applied.backup_path)
    assert path.read_bytes() == original_bytes


def test_config_migration_requires_explicit_mode_change(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("knowledge_workflow:\n  mode: wiki_first\n", encoding="utf-8")

    applied = VerifiedHybridConfigMigrator(path).apply(target_mode="verified")

    assert applied.changed is True
    assert "mode: verified" in path.read_text(encoding="utf-8")
