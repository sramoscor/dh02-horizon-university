"""Comandos operativos: `python -m backend.bootstrap.cli {wait-db|migrate|seed|demo-reset}`."""

import subprocess
import sys
import time

from sqlalchemy import text

from backend.infrastructure.clock import SystemClock
from backend.infrastructure.seeding import reset, seed
from backend.infrastructure.settings import Settings
from backend.infrastructure.sql import make_engine


def wait_db(engine, seconds: int = 60) -> None:
    deadline = time.monotonic() + seconds
    while True:
        try:
            with engine.connect() as c:
                c.execute(text("SELECT 1"))
            return
        except Exception:
            if time.monotonic() > deadline:
                raise
            time.sleep(1)


def main(argv: list[str]) -> int:
    command = argv[1] if len(argv) > 1 else "help"
    settings = Settings()
    engine = make_engine(settings.database_url, pool_size=2)
    if command == "wait-db":
        wait_db(engine)
    elif command == "migrate":
        wait_db(engine)
        return subprocess.call([sys.executable, "-m", "alembic", "upgrade", "head"])
    elif command == "seed":
        print(seed(engine, SystemClock().now()))
    elif command == "demo-reset":
        print(reset(engine, SystemClock().now()))
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
