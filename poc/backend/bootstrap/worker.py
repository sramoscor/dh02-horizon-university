"""Punto de entrada de C-WORKER: `python -m backend.bootstrap.worker` (mismo código que C-API, E2-ADR-002).

Tareas: entregas del outbox con lease y reintentos (QS-02) y refresco de la proyección del directorio.
No crea ni modifica órdenes por su cuenta (§5.2).
"""

import logging
import signal
import time
from pathlib import Path

from backend.application.delivery import DeliveryService, DeliverySettings, DirectoryRefreshService
from backend.infrastructure.adapters import HttpDirectoryAdapter, HttpNotificationAdapter
from backend.infrastructure.clock import SystemClock
from backend.infrastructure.settings import Settings
from backend.infrastructure.sql import SqlDeliveryStore, SqlUnitOfWorkFactory, make_engine

log = logging.getLogger("dh02.worker")
HEARTBEAT = Path("/tmp/dh02-worker-heartbeat")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = Settings()
    engine = make_engine(settings.database_url, pool_size=3)
    clock = SystemClock()
    delivery_settings = DeliverySettings(settings.worker_lease_seconds, settings.worker_backoff_seconds,
                                         settings.worker_max_attempts)
    deliveries = DeliveryService(SqlDeliveryStore(engine),
                                 HttpNotificationAdapter(settings.xnotify_url, settings.notify_timeout_seconds),
                                 clock, delivery_settings)
    directory = DirectoryRefreshService(SqlUnitOfWorkFactory(engine), HttpDirectoryAdapter(settings.xdir_url), clock)
    log.info("worker_started poll=%ss lease=%ss backoff=%s max_attempts=%s timeout=%ss",
             settings.worker_poll_seconds, settings.worker_lease_seconds, settings.worker_backoff_seconds,
             settings.worker_max_attempts, settings.notify_timeout_seconds)

    running = True

    def stop(*_):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    next_directory = 0.0
    while running:
        started = time.monotonic()
        try:
            report = deliveries.run_once()
            if report.claimed:
                log.info("deliveries claimed=%s delivered=%s retry=%s dead=%s stale=%s",
                         report.claimed, report.delivered, report.retried, report.dead, report.stale_leases)
        except Exception:
            log.exception("delivery_cycle_failed")
        if started >= next_directory:
            try:
                log.info("directory_refresh %s", directory.refresh())
            except Exception as exc:  # QS-09: se opera con la última proyección vigente
                log.warning("directory_refresh_failed %s", type(exc).__name__)
            next_directory = started + settings.directory_refresh_seconds
        HEARTBEAT.write_text(str(time.time()))
        time.sleep(max(0.0, settings.worker_poll_seconds - (time.monotonic() - started)))
    log.info("worker_stopped")


if __name__ == "__main__":
    main()
