"""Нормализация дат дедлайнов из интерфейса еКурсов СФУ.

Модуль намеренно консервативен: если дату нельзя определить однозначно,
``due_at`` остаётся ``None``, а причина попадает в ``warnings``.
Ничего не додумываем (год, часовой пояс, время) молча.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Iterable

__all__ = ["ParsedDue", "parse_due_date", "MONTHS", "WEEKDAYS"]


MONTHS: dict[str, int] = {
    "января": 1,
    "январь": 1,
    "февраля": 2,
    "февраль": 2,
    "марта": 3,
    "март": 3,
    "апреля": 4,
    "апрель": 4,
    "мая": 5,
    "май": 5,
    "июня": 6,
    "июнь": 6,
    "июля": 7,
    "июль": 7,
    "августа": 8,
    "август": 8,
    "сентября": 9,
    "сентябрь": 9,
    "октября": 10,
    "октябрь": 10,
    "ноября": 11,
    "ноябрь": 11,
    "декабря": 12,
    "декабрь": 12,
}

WEEKDAYS: dict[str, int] = {
    "понедельник": 0,
    "вторник": 1,
    "среда": 2,
    "среду": 2,
    "четверг": 3,
    "пятница": 4,
    "пятницу": 4,
    "суббота": 5,
    "субботу": 5,
    "воскресенье": 6,
    "воскресение": 6,
    "пн": 0,
    "вт": 1,
    "ср": 2,
    "чт": 3,
    "пт": 4,
    "сб": 5,
    "вс": 6,
}

_WEEKDAY_WORDS = tuple(sorted(WEEKDAYS, key=len, reverse=True))
_MONTH_WORDS = tuple(sorted(MONTHS, key=len, reverse=True))

_TIME_RE = re.compile(r"(?<!\d)(\d{1,2}):(\d{2})(?!\d)")
_YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
_DAY_RE = re.compile(r"(?<!\d)(\d{1,2})(?!\d)")


@dataclass
class ParsedDue:
    """Результат разбора одной строки дедлайна."""

    due_at: str | None = None
    warnings: list[str] = field(default_factory=list)


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _find_month(text: str) -> int | None:
    for word in _MONTH_WORDS:
        if re.search(r"\b" + word + r"\b", text):
            return MONTHS[word]
    return None


def _find_weekday(text: str) -> int | None:
    for word in _WEEKDAY_WORDS:
        if re.search(r"\b" + word + r"\b", text):
            return WEEKDAYS[word]
    return None


def _candidate_years(today: date, span_back: int = 2, span_fwd: int = 3) -> Iterable[int]:
    for year in range(today.year - span_back, today.year + span_fwd + 1):
        yield year


def _normalize_datetime_attr(value: str) -> str | None:
    """Преобразует значение ``time[datetime]`` в ISO без смещения (настенное время сайта)."""
    value = _collapse(value)
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    # Часовой пояс сайта не выдумываем: сохраняем настенное время как naive.
    return parsed.replace(tzinfo=None).isoformat(timespec="minutes")


def parse_due_date(
    raw: str | None,
    *,
    datetime_attr: str | None = None,
    now: datetime | None = None,
) -> ParsedDue:
    """Разбирает отображаемый дедлайн в ISO-строку или помечает его как неоднозначный.

    Приоритет отдаётся машинному значению ``datetime_attr`` (например, из
    ``time[datetime]``). Иначе строка разбирается эвристически.
    """
    now = now or datetime.now()

    if datetime_attr:
        normalized = _normalize_datetime_attr(datetime_attr)
        if normalized:
            return ParsedDue(due_at=normalized)

    text = _collapse(raw)
    if not text:
        return ParsedDue(due_at=None, warnings=["due_date_empty"])

    low = text.lower()
    warnings: list[str] = []

    # --- время ---
    time_match = _TIME_RE.search(low)
    if time_match:
        hour, minute = int(time_match.group(1)), int(time_match.group(2))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return ParsedDue(due_at=None, warnings=["due_time_invalid"])
        rest = low[: time_match.start()] + " " + low[time_match.end():]
    else:
        hour, minute = 0, 0
        rest = low
        warnings.append("due_time_missing")

    # --- год ---
    year_match = _YEAR_RE.search(rest)
    year = int(year_match.group(1)) if year_match else None
    if year_match:
        rest = rest[: year_match.start()] + " " + rest[year_match.end():]

    # --- день ---
    day_match = _DAY_RE.search(rest)
    day = int(day_match.group(1)) if day_match else None

    month = _find_month(low)
    weekday = _find_weekday(low)

    # --- относительные даты (Сегодня/Завтра) ---
    if "послезавтра" in low:
        base = now.date() + timedelta(days=2)
        return _build(base.year, base.month, base.day, hour, minute, warnings, weekday)
    if "завтра" in low:
        base = now.date() + timedelta(days=1)
        return _build(base.year, base.month, base.day, hour, minute, warnings, weekday)
    if "сегодня" in low:
        base = now.date()
        return _build(base.year, base.month, base.day, hour, minute, warnings, weekday)

    if month is None or day is None:
        return ParsedDue(due_at=None, warnings=warnings + ["due_date_unrecognized"])

    if year is not None:
        return _build(year, month, day, hour, minute, warnings, weekday)

    # Год отсутствует — пытаемся однозначно восстановить его по дню недели.
    if weekday is None:
        warnings.append("year_missing")
        return ParsedDue(due_at=None, warnings=warnings)

    matches = []
    for candidate in _candidate_years(now.date()):
        try:
            d = date(candidate, month, day)
        except ValueError:
            continue
        if d.weekday() == weekday:
            matches.append(candidate)

    if len(matches) == 1:
        warnings.append("year_inferred_from_weekday")
        return _build(matches[0], month, day, hour, minute, warnings, weekday)
    if not matches:
        warnings.append("year_missing")
        return ParsedDue(due_at=None, warnings=warnings)

    warnings.append("year_ambiguous")
    return ParsedDue(due_at=None, warnings=warnings)


def _build(
    year: int,
    month: int,
    day: int,
    hour: int,
    minute: int,
    warnings: list[str],
    weekday: int | None,
) -> ParsedDue:
    try:
        value = datetime(year, month, day, hour, minute)
    except ValueError:
        return ParsedDue(due_at=None, warnings=list(warnings) + ["due_date_invalid"])

    result = list(warnings)
    if weekday is not None and value.weekday() != weekday:
        result.append("weekday_mismatch")
    return ParsedDue(due_at=value.isoformat(timespec="minutes"), warnings=result)
