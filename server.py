"""MCP-сервер еКурсов СФУ (Moodle).

Запуск: ``python server.py`` (транспорт stdio).

Диагностика идёт только в stderr, stdout зарезервирован под протокол MCP.
Секреты (пароль, cookies, sesskey) никогда не возвращаются клиенту.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from sfu_client import SFUClient, SFUError
from sfu_env import load_dotenv

load_dotenv()  # подхватываем локальный .env, если он есть (окружение имеет приоритет)

logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("sfu.server")

server = MCPServer(
    "sfu-ecourses",
    instructions=(
        "Инструменты личного кабинета студента еКурсов СФУ (Moodle), только чтение. "
        "Сначала вызывайте get_courses, затем используйте course_id в других инструментах."
    ),
)

_client: SFUClient | None = None


def get_client() -> SFUClient:
    global _client
    if _client is None:
        _client = SFUClient()
    return _client


def _call(func, *args, **kwargs):
    """Преобразует ожидаемые ошибки в понятное сообщение для MCP-клиента."""
    try:
        return func(*args, **kwargs)
    except ToolError:
        raise
    except (SFUError, ValueError) as exc:
        raise ToolError(str(exc)) from exc


@server.tool(
    name="get_courses",
    description="Список всех доступных студенту курсов: id, название, короткое имя, URL.",
)
def get_courses() -> list[dict[str, Any]]:
    return _call(get_client().get_courses)


@server.tool(
    name="get_grades",
    description=(
        "Оценки одного курса по course_id: строки таблицы оценок с типом элемента, "
        "исходной и числовой оценкой, диапазоном, признаком итоговых строк и предупреждениями."
    ),
)
def get_grades(course_id: int) -> list[dict[str, Any]]:
    return _call(get_client().get_grades, course_id)


@server.tool(
    name="get_assignments",
    description=(
        "Задания одного курса по course_id: название, module_id/URL, срок сдачи, "
        "состояние ответа, оценка и предупреждения о нераспознанных датах."
    ),
)
def get_assignments(course_id: int) -> list[dict[str, Any]]:
    return _call(get_client().get_assignments, course_id)


@server.tool(
    name="get_upcoming_deadlines",
    description=(
        "Дедлайны по всем доступным курсам в ближайшие days дней (1–365). "
        "include_submitted=true добавляет уже отправленные работы. "
        "include_overdue=true переносит просроченные из списка overdue в items. "
        "Неизвестный статус не трактуется как «не сдано». Ошибки отдельных курсов "
        "возвращаются в course_errors и не прерывают общий отчёт."
    ),
)
def get_upcoming_deadlines(
    days: int = 7, include_submitted: bool = False, include_overdue: bool = False
) -> dict[str, Any]:
    return _call(get_client().get_upcoming_deadlines, days, include_submitted, include_overdue)


@server.tool(
    name="get_assignment_details",
    description=(
        "Детали одного задания по module_id: заголовок, описание, срок сдачи, "
        "состояние ответа и оценивания, последнее изменение, прикреплённые файлы."
    ),
)
def get_assignment_details(module_id: int) -> dict[str, Any]:
    return _call(get_client().get_assignment_details, module_id)


def main() -> None:
    try:
        server.run(transport="stdio")
    except SFUError as exc:  # pragma: no cover - защита от неожиданного падения
        log.error("Ошибка еКурсов: %s", exc)
        raise


if __name__ == "__main__":
    main()
