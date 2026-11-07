"""Calendario de días hábiles del campus (E2-AS-02; E2-AS-09: fechas en UTC)."""

from datetime import date, datetime, timedelta


def add_business_days(start: datetime, days: int, holidays: frozenset[date] = frozenset()) -> datetime:
    """Suma `days` días hábiles (lunes a viernes, sin feriados configurados) conservando la hora."""
    current = start
    remaining = days
    while remaining > 0:
        current += timedelta(days=1)
        if current.weekday() < 5 and current.date() not in holidays:
            remaining -= 1
    return current


def parse_holidays(raw: str) -> frozenset[date]:
    return frozenset(date.fromisoformat(d.strip()) for d in raw.split(",") if d.strip())
