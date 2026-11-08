"""Entorno de Alembic. La URL viene de DATABASE_URL (o de -x url=...); sin rutas locales."""

import os

from alembic import context
from sqlalchemy import create_engine

config = context.config
url = context.get_x_argument(as_dictionary=True).get("url") or os.environ["DATABASE_URL"]


def run_migrations_online() -> None:
    engine = create_engine(url)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


run_migrations_online()
