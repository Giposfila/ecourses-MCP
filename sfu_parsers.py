"""Чистые парсеры HTML/JSON еКурсов СФУ.

Все функции работают с уже полученными строками и не выполняют сетевых
запросов. DOM не мутируется: для очистки ячеек создаются копии.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from sfu_dates import ParsedDue, parse_due_date, parse_due_timestamp

__all__ = [
    "ParseError",
    "BASE_URL",
    "normalize_course",
    "parse_courses_ajax_response",
    "parse_grades",
    "parse_assignments",
    "parse_assignment_details",
]

BASE_URL = "https://e.sfu-kras.ru"

# ---------------------------------------------------------------------------
# Общие утилиты
# ---------------------------------------------------------------------------


class ParseError(RuntimeError):
    """Структура страницы не совпала с ожидаемой."""


def _norm(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _norm_key(text: str | None) -> str:
    return _norm(text).lower().replace("\u00a0", " ")


def _clean_cell(cell, drop_selectors: tuple[str, ...] = ()) -> str:
    """Возвращает текст ячейки без служебных элементов, не меняя исходный DOM."""
    if cell is None:
        return ""
    clone = BeautifulSoup(str(cell), "html.parser")
    for selector in drop_selectors:
        for element in clone.select(selector):
            element.decompose()
    return _norm(clone.get_text(" ", strip=True))


def _to_number(text: str | None) -> float | None:
    """Извлекает число из оценки.

    Moodle показывает шкальные оценки как «отлично (100)»: текст сохраняется
    в ``grade_raw``, а числовой эквивалент берётся из скобок.
    """
    if text is None:
        return None
    value = text.replace("\u00a0", " ").strip()
    match = re.fullmatch(r"[-+]?\d+(?:[.,]\d+)?", value)
    if match is None:
        match = re.search(r"\(([-+]?\d+(?:[.,]\d+)?)\)\s*$", value)
        if match is None:
            return None
    try:
        return float(match.group(0).strip("()").replace(",", "."))
    except ValueError:
        return None


def _empty_or_dash(text: str | None) -> bool:
    return _norm(text) in {"", "-", "–", "—", "−"}


def _parse_range(text: str | None) -> tuple[float | None, float | None]:
    if not text:
        return None, None
    parts = re.split(r"[-–—−]", text.replace("\u00a0", " "))
    if len(parts) == 2:
        return _to_number(parts[0]), _to_number(parts[1])
    return None, None


# ---------------------------------------------------------------------------
# Курсы (AJAX)
# ---------------------------------------------------------------------------


def parse_courses_ajax_response(result: dict[str, Any]) -> tuple[list[dict], dict | None]:
    """Разбирает один элемент ответа ``lib/ajax/service.php``.

    Возвращает ``(курсы, ошибка)``; при ошибке список курсов пуст.
    """
    if not isinstance(result, dict):
        return [], {"errorcode": "malformed", "exception": "Некорректный формат ответа Moodle"}

    if result.get("error"):
        return [], {
            "errorcode": result.get("errorcode"),
            "exception": result.get("exception") or "Неизвестная ошибка Moodle",
        }

    data = result.get("data") or {}
    courses = data.get("courses") or []
    if not isinstance(courses, list):
        return [], {"errorcode": "malformed", "exception": "Поле data.courses не является списком"}
    return courses, None


def normalize_course(raw: dict[str, Any]) -> dict[str, Any]:
    course_id = raw.get("id")
    try:
        course_id = int(course_id)
    except (TypeError, ValueError):
        course_id = raw.get("id")

    return {
        "id": course_id,
        "name": raw.get("fullname"),
        "shortname": raw.get("shortname"),
        "url": raw.get("viewurl"),
        "category": raw.get("coursecategory"),
        "progress": raw.get("progress"),
        "has_progress": bool(raw.get("hasprogress")),
        "start_date": raw.get("startdate"),
        "end_date": raw.get("enddate"),
        "hidden": bool(raw.get("hidden")),
        "visible": raw.get("visible"),
    }


# ---------------------------------------------------------------------------
# Оценки
# ---------------------------------------------------------------------------

_GRADE_COLUMNS = {
    "элемент оценивания": "name",
    "оценка": "grade",
    "диапазон": "range",
    "место": "position",
    "отзыв": "feedback",
    "вклад в итог курса": "contribution",
}

_GRADE_DEFAULT_COLUMNS = {
    "name": 0,
    "grade": 1,
    "range": 2,
    "position": 3,
    "feedback": 4,
    "contribution": 5,
}


def _classify_grade_item(name: str, row_classes: list[str]) -> tuple[str, bool]:
    low = name.lower()
    if "итоговая оценка" in low:
        return "final_grade", True
    if "вычисляемая оценка" in low:
        return "computed_grade", True
    if "итог категории" in low:
        return "category_total", True
    if "category" in row_classes:
        return "category_total", True
    if re.match(r"^тест\b", low):
        return "test", False
    if re.match(r"^задани", low):
        return "assignment", False
    if re.match(r"^итог\b", low) or re.match(r"^всего\b", low):
        return "total", True
    return "unknown", False


def _strip_item_prefix(name: str) -> str:
    """Убирает служебные префиксы Moodle, сохраняя содержательный остаток."""
    for prefix in ("Итоговая оценка ", "Вычисляемая оценка ", "Задание ", "Тест "):
        if name.startswith(prefix):
            remainder = name[len(prefix):].strip()
            if remainder:
                return remainder
    return name


def parse_grades(html: str) -> list[dict[str, Any]]:
    """Разбирает ``table.user-grade`` в список строк оценок."""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.select_one("table.user-grade")
    if table is None:
        raise ParseError("Таблица оценок (table.user-grade) не найдена")

    columns = dict(_GRADE_DEFAULT_COLUMNS)
    thead = table.find("thead")
    if thead is not None:
        mapped: dict[str, int] = {}
        for index, cell in enumerate(thead.find_all("th")):
            key = _GRADE_COLUMNS.get(_norm_key(cell.get_text(" ", strip=True)))
            if key:
                mapped[key] = index
        if mapped:
            columns.update(mapped)

    body = table.find("tbody")
    rows = body.find_all("tr") if body is not None else table.find_all("tr")

    results: list[dict[str, Any]] = []
    current_category: str | None = None

    for row in rows:
        cells = row.find_all(["th", "td"], recursive=False)
        if not cells:
            continue

        row_classes = [str(c) for c in (row.get("class") or [])]

        # Одиночная ячейка — заголовок раздела/категории.
        if len(cells) == 1:
            current_category = _clean_cell(cells[0]) or current_category
            continue

        name_idx = columns["name"]
        if name_idx >= len(cells):
            continue

        raw_name = _clean_cell(cells[name_idx], drop_selectors=(".accesshide", ".sr-only"))
        if not raw_name:
            continue
        if _norm_key(raw_name) == "элемент оценивания":
            continue

        item_type, is_total = _classify_grade_item(raw_name, row_classes)
        name = _strip_item_prefix(raw_name)

        def _cell(key: str) -> str:
            idx = columns.get(key)
            if idx is None or idx >= len(cells):
                return ""
            return cells[idx]

        grade_text = _clean_cell(
            _cell("grade"),
            drop_selectors=("a", "button", ".action-menu", ".accesshide", ".sr-only"),
        )
        grade_text = _norm(
            grade_text.replace("Действия", "").replace("Анализ оценок", "")
        )
        grade_raw = None if _empty_or_dash(grade_text) else grade_text

        range_text = _clean_cell(_cell("range"))
        range_raw = None if _empty_or_dash(range_text) else range_text
        range_min, range_max = _parse_range(range_raw)

        position = _clean_cell(_cell("position"))
        feedback = _clean_cell(_cell("feedback"))
        contribution = _clean_cell(_cell("contribution"))

        warnings: list[str] = []
        if grade_raw is None and not is_total:
            warnings.append("grade_missing")
        if is_total and grade_raw is None:
            warnings.append("total_without_grade")

        results.append(
            {
                "name": name or raw_name,
                "raw_name": raw_name,
                "item_type": item_type,
                "category": current_category,
                "grade_raw": grade_raw,
                "grade_numeric": _to_number(grade_raw),
                "range_raw": range_raw,
                "range_min": range_min,
                "range_max": range_max,
                "position": None if _empty_or_dash(position) else position,
                "feedback": None if _empty_or_dash(feedback) else feedback,
                "contribution": None if _empty_or_dash(contribution) else contribution,
                "is_total": is_total,
                "warnings": warnings,
            }
        )

    return results


# ---------------------------------------------------------------------------
# Задания
# ---------------------------------------------------------------------------

_ASSIGN_COLUMNS = {
    "имя": "name",
    "последний срок сдачи": "due",
    "состояние ответа на задание": "status",
    "оценка": "grade",
}

_ASSIGN_DEFAULT_COLUMNS = {"name": 0, "due": 1, "status": 2, "grade": 3}

# Значения data-mdl-overview-item -> наши поля (ключ «Оценка» приходит кириллицей).
_OVERVIEW_ITEM_MAP = {
    "name": "name",
    "submissionstatus": "status",
    "duedate": "due",
    "grade": "grade",
    "оценка": "grade",
}

# Строки-заголовки, которые нельзя принимать за задание.
_HEADER_NAMES = {"имя", "элемент оценивания", "name", "item"}


def _extract_cmid(href: str | None) -> int | None:
    if not href:
        return None
    if "/mod/" not in href:
        return None
    match = re.search(r"[?&]id=(\d+)", href)
    return int(match.group(1)) if match else None


def map_submission_status(text: str | None) -> str:
    """Переводит отображаемый статус в стабильное значение.

    Пустое значение — ``unknown``, а не ``not_submitted``: сайт мог просто
    не раскрыть статус.
    """
    low = _norm_key(text)
    if not low:
        return "unknown"
    if "нет ответ" in low or "не отправлено" in low or "не сдан" in low:
        return "not_submitted"
    if "черновик" in low:
        return "draft"
    if "отправлено" in low or "сдан" in low or "оцениван" in low:
        return "submitted"
    return "unknown"


def _find_datetime_attr(cell) -> str | None:
    if cell is None:
        return None
    node = cell.find("time")
    if node is not None and node.get("datetime"):
        return node["datetime"]
    for node in cell.find_all(attrs={"datetime": True}):
        if node.get("datetime"):
            return node["datetime"]
    return None


def _overview_item_cells(row) -> dict[str, Any]:
    """Собирает ячейки строки по атрибуту ``data-mdl-overview-item``.

    Тема СФУ проставляет этот атрибут на каждую ячейку таблицы обзора,
    поэтому он надёжнее порядка столбцов и текста заголовков.
    """
    cells: dict[str, Any] = {}
    for cell in row.find_all(["th", "td"], recursive=False):
        raw_key = cell.get("data-mdl-overview-item")
        if not raw_key:
            continue
        mapped = _OVERVIEW_ITEM_MAP.get(str(raw_key).strip().casefold())
        if mapped and mapped not in cells:
            cells[mapped] = cell
    return cells


def _extract_section(cell) -> str | None:
    if cell is None:
        return None
    node = cell.select_one("div.small")
    if node is None:
        return None
    text = _norm(node.get_text(" ", strip=True))
    return text or None


def _parse_overview_due(cell, *, now: datetime | None) -> tuple[str | None, ParsedDue]:
    """Разбирает срок сдачи: сначала абсолютный таймстамп, затем текст."""
    if cell is None:
        return None, ParsedDue(due_at=None, warnings=[])
    text = _norm(cell.get_text(" ", strip=True)) or None
    if text is not None and _empty_or_dash(text):
        text = None
    timestamp = cell.get("data-mdl-overview-value")
    if timestamp is not None and str(timestamp).strip().isdigit():
        return text, parse_due_timestamp(timestamp)
    return text, parse_due_date(text, datetime_attr=_find_datetime_attr(cell), now=now)


def _extract_overview_grade(cell) -> tuple[str | None, float | None]:
    """Возвращает (человекочитаемая оценка, число) для ячейки оценки."""
    if cell is None:
        return None, None
    attr_value = cell.get("data-mdl-overview-value")
    text = _clean_cell(
        cell, drop_selectors=("a", "button", ".action-menu", ".accesshide", ".sr-only")
    )
    text = _norm(text.replace("Действия", "").replace("Анализ оценок", ""))

    grade_raw = None
    if text and not _empty_or_dash(text):
        grade_raw = text
    elif attr_value is not None and not _empty_or_dash(str(attr_value)):
        grade_raw = _norm(str(attr_value))

    grade_numeric = _to_number(str(attr_value)) if attr_value is not None else None
    if grade_numeric is None and grade_raw is not None:
        grade_numeric = _to_number(grade_raw)
    return grade_raw, grade_numeric


def _parse_overview_row(row, item_cells: dict[str, Any], *, now: datetime | None) -> dict[str, Any] | None:
    name_cell = item_cells.get("name")
    if name_cell is None:
        return None

    name = _norm(name_cell.get("data-mdl-overview-value") or name_cell.get_text(" ", strip=True))
    if not name:
        return None

    cmid_raw = row.get("data-mdl-overview-cmid")
    module_id = None
    if cmid_raw is not None and str(cmid_raw).strip().isdigit():
        module_id = int(cmid_raw)
    if module_id is None:
        link = name_cell.find("a", href=True)
        if link is not None:
            module_id = _extract_cmid(link.get("href"))
    url = f"{BASE_URL}/mod/assign/view.php?id={module_id}" if module_id else None

    due_raw, parsed_due = _parse_overview_due(item_cells.get("due"), now=now)

    status_cell = item_cells.get("status")
    status_raw = None
    if status_cell is not None:
        status_raw = _norm(
            status_cell.get("data-mdl-overview-value") or status_cell.get_text(" ", strip=True)
        ) or None

    grade_raw, grade_numeric = _extract_overview_grade(item_cells.get("grade"))

    return {
        "name": name,
        "module_id": module_id,
        "url": url,
        "section": _extract_section(name_cell),
        "due_date_raw": due_raw,
        "due_at": parsed_due.due_at,
        "due_warnings": parsed_due.warnings,
        "submission_status_raw": status_raw,
        "submission_status": map_submission_status(status_raw),
        "grade_raw": grade_raw,
        "grade_numeric": grade_numeric,
    }


def _resolve_assign_columns(table) -> dict[str, int]:
    """Определяет индексы столбцов по заголовкам.

    Если строка заголовков распознана, используются ТОЛЬКО найденные столбцы:
    отсутствующий столбец (например «Последний срок сдачи») остаётся ``None``
    и не подменяется чужой ячейкой по позиции.
    """
    thead = table.find("thead")
    mapped: dict[str, int] = {}
    if thead is not None:
        for index, cell in enumerate(thead.find_all("th")):
            key = _ASSIGN_COLUMNS.get(_norm_key(cell.get_text(" ", strip=True)))
            if key and key not in mapped:
                mapped[key] = index
    if "name" in mapped:
        return mapped
    return dict(_ASSIGN_DEFAULT_COLUMNS)


def parse_assignments(html: str, *, now: datetime | None = None) -> list[dict[str, Any]]:
    """Разбирает ``table.course-overview-table`` со сроками и статусами заданий.

    Основной путь — атрибуты ``data-mdl-overview-*`` (их проставляет тема СФУ).
    Если их нет, используется разбор по заголовкам столбцов.
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.select_one("table.course-overview-table")
    if table is None:
        raise ParseError("Таблица заданий (table.course-overview-table) не найдена")

    columns = _resolve_assign_columns(table)

    body = table.find("tbody")
    rows = body.find_all("tr") if body is not None else table.find_all("tr")

    results: list[dict[str, Any]] = []
    section: str | None = None

    for row in rows:
        item_cells = _overview_item_cells(row)
        if item_cells:
            parsed = _parse_overview_row(row, item_cells, now=now)
            if parsed is not None:
                results.append(parsed)
            continue

        cells = row.find_all(["th", "td"], recursive=False)
        if not cells:
            continue
        if len(cells) == 1:
            section = _clean_cell(cells[0]) or section
            continue

        def _cell(key: str):
            idx = columns.get(key)
            if idx is None or idx >= len(cells):
                return None
            return cells[idx]

        name_cell = _cell("name")
        if name_cell is None:
            continue

        link = name_cell.find("a", href=True)
        module_id = None
        url = None
        if link is not None:
            module_id = _extract_cmid(link.get("href"))
            if module_id is not None:
                url = urljoin(BASE_URL + "/", link.get("href"))
            name = _norm(link.get_text(" ", strip=True))
        else:
            name = _clean_cell(name_cell, drop_selectors=(".accesshide", ".sr-only"))

        if not name or _norm_key(name) in _HEADER_NAMES:
            continue

        due_cell = _cell("due")
        due_raw, parsed_due = _parse_overview_due(due_cell, now=now)

        status_cell = _cell("status")
        status_raw = _clean_cell(status_cell, drop_selectors=(".accesshide", ".sr-only"))
        status_raw_value = None if _empty_or_dash(status_raw) else status_raw

        grade_raw, grade_numeric = _extract_overview_grade(_cell("grade"))

        results.append(
            {
                "name": name,
                "module_id": module_id,
                "url": url,
                "section": section,
                "due_date_raw": due_raw or None,
                "due_at": parsed_due.due_at,
                "due_warnings": parsed_due.warnings,
                "submission_status_raw": status_raw_value,
                "submission_status": map_submission_status(status_raw_value),
                "grade_raw": grade_raw,
                "grade_numeric": grade_numeric,
            }
        )

    return results


# ---------------------------------------------------------------------------
# Детали задания
# ---------------------------------------------------------------------------

_STATUS_LABELS = {
    "срок сдачи": "due_date_raw",
    "состояние ответа на задание": "submission_status_raw",
    "состояние оценивания": "grading_status_raw",
    "последнее изменение": "last_modified_raw",
}


def parse_assignment_details(
    html: str,
    *,
    module_id: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Извлекает детали одного задания со страницы ``mod/assign/view.php``."""
    soup = BeautifulSoup(html, "html.parser")
    warnings: list[str] = []

    title_node = soup.find("h1")
    title = _norm(title_node.get_text(" ", strip=True)) if title_node else None

    description = None
    for selector in ("#intro", "div.assignintro", "div.box.generalbox"):
        node = soup.select_one(selector)
        if node is not None and _norm(node.get_text(" ", strip=True)):
            description = _norm(node.get_text(" ", strip=True))
            break

    status_table = None
    for table in soup.find_all("table"):
        text = _norm_key(table.get_text(" ", strip=True))
        if "состояние ответа на задание" in text or "состояние оценивания" in text:
            status_table = table
            break

    fields: dict[str, str | None] = {}
    if status_table is None:
        warnings.append("status_table_not_found")
    else:
        for row in status_table.find_all("tr"):
            cells = row.find_all(["th", "td"], recursive=False)
            if len(cells) < 2:
                continue
            label = _norm_key(cells[0].get_text(" ", strip=True))
            value = _clean_cell(cells[1], drop_selectors=(".accesshide", ".sr-only"))
            for known, key in _STATUS_LABELS.items():
                if label.startswith(known):
                    fields[key] = None if _empty_or_dash(value) else value
                    break

    due_datetime_attr = None
    if status_table is not None:
        due_datetime_attr = _find_datetime_attr(status_table)
    parsed_due = parse_due_date(
        fields.get("due_date_raw"),
        datetime_attr=due_datetime_attr,
        now=now,
    )

    attachments: list[dict[str, str]] = []
    for link in soup.find_all("a", href=True):
        href = link.get("href") or ""
        if "/pluginfile.php/" in href:
            attachments.append(
                {
                    "name": _norm(link.get_text(" ", strip=True)),
                    "url": urljoin(BASE_URL + "/", href),
                }
            )

    return {
        "module_id": module_id,
        "title": title,
        "description": description,
        "due_date_raw": fields.get("due_date_raw"),
        "due_at": parsed_due.due_at,
        "due_warnings": parsed_due.warnings,
        "submission_status_raw": fields.get("submission_status_raw"),
        "submission_status": map_submission_status(fields.get("submission_status_raw")),
        "grading_status_raw": fields.get("grading_status_raw"),
        "last_modified_raw": fields.get("last_modified_raw"),
        "attachments": attachments,
        "warnings": warnings,
    }
