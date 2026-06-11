import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Import brave models so Alembic can see the metadata.
# This is the load-bearing link: Base.metadata -> target_metadata
from brave.core.models import Base  # noqa: E402

target_metadata = Base.metadata


def get_db_url() -> str:
    """Read the database URL from the environment variable BRAVE_DB_URL.

    Falls back to the sqlalchemy.url from alembic.ini for backwards-compatibility
    with direct `alembic` invocations that don't set the env var.
    """
    url = os.environ.get("BRAVE_DB_URL")
    if url:
        return url
    # Fall back to alembic.ini value
    return config.get_main_option("sqlalchemy.url")  # type: ignore[return-value]


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL and not an Engine,
    though an Engine is acceptable here as well. By skipping the Engine
    creation we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.
    """
    url = get_db_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine and associate a
    connection with the context.
    """
    # Override the sqlalchemy.url from the config with our env-based URL
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_db_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # Disable per-migration transactions so that CREATE INDEX CONCURRENTLY
            # in migration 0002 can run without a surrounding transaction block.
            # (PostgreSQL does not allow CONCURRENTLY inside a transaction.)
            transaction_per_migration=False,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
