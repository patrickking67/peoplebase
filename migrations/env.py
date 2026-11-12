"""Alembic environment. Flask-Migrate runs this inside an application context,
so the engine and metadata come from the app's SQLAlchemy extension."""

from logging.config import fileConfig

from alembic import context
from flask import current_app

config = context.config
if config.config_file_name:
    # Show "Running upgrade 0001 -> 0002" lines without silencing the app's loggers.
    fileConfig(config.config_file_name, disable_existing_loggers=False)
migrate = current_app.extensions["migrate"]
target_metadata = migrate.db.metadata


def run_migrations_offline():
    """Write SQL to stdout instead of running it: flask --app app db upgrade --sql"""
    url = migrate.db.engine.url.render_as_string(hide_password=False)
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True, **migrate.configure_args)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    with migrate.db.engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, **migrate.configure_args)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
