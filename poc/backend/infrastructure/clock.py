"""Relojes (puerto `Clock`). E2-AS-09: todo en UTC."""

from datetime import UTC, datetime, timedelta


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class OffsetClock:
    """Reloj del sistema desplazado: permite a la demo "adelantar el tiempo" sin tocar datos."""

    def __init__(self) -> None:
        self.offset = timedelta()

    def now(self) -> datetime:
        return datetime.now(UTC) + self.offset


class FakeClock:
    """Reloj controlable para pruebas (T-SLA-01, E2-AS-02)."""

    def __init__(self, start: datetime) -> None:
        self.current = start

    def now(self) -> datetime:
        return self.current

    def advance(self, **kwargs: float) -> None:
        self.current += timedelta(**kwargs)
