"""Alembic environment — honors explicit test URL overrides (WP4).

URL resolution priority:
  1. ``-x url=...`` command-line option
  2. ``SHINEHE_TEST_ALEMBIC_URL`` environment variable
  3. ``sqlalchemy.url`` already set in alembic.ini / config
  4. ShineHe Config db path
"""
from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from alembic import context

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _resolve_sqlalchemy_url() -> str:
    # 1) alembic -x url=sqlite:///...
    x_args = context.get_x_argument(as_dictionary=True)
    if x_args.get("url"):
        return str(x_args["url"])

    # 2) explicit test env
    test_url = os.environ.get("SHINEHE_TEST_ALEMBIC_URL")
    if test_url:
        return test_url.strip()

    # 3) existing config option (may come from alembic.ini)
    existing = config.get_main_option("sqlalchemy.url")
    if existing and existing.strip() and "driver://" not in existing:
        # skip placeholder-ish defaults
        if not existing.startswith("driver:"):
            return existing.strip()

    # 4) ShineHe Config
    from src.utils.config import Config

    Config.load()
    db_path = Config.get_db_path()
    return f"sqlite:///{Path(db_path).as_posix()}"


_url = _resolve_sqlalchemy_url()
config.set_main_option("sqlalchemy.url", _url)

target_metadata = None


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
