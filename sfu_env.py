"""Минимальная загрузка локального файла ``.env``.

Без внешних зависимостей. Значения секретов никогда не логируются.
Переменные окружения, заданные явно в оболочке/MCP-клиенте, имеют приоритет
над значениями из файла.
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = ["load_dotenv", "DEFAULT_ENV_PATH"]

DEFAULT_ENV_PATH = Path(__file__).resolve().parent / ".env"


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def load_dotenv(path: str | os.PathLike | None = None, *, override: bool = False) -> dict[str, str]:
    """Читает ``.env`` и добавляет переменные в ``os.environ``.

    Возвращает только те пары, что были прочитаны из файла (без вывода наружу).
    Поддерживаются строки ``KEY=VALUE``, комментарии ``#``, префикс ``export``
    и значения в одинарных/двойных кавычках.
    """
    env_path = Path(path) if path is not None else DEFAULT_ENV_PATH
    if not env_path.is_file():
        return {}

    loaded: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue

        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        value = _strip_quotes(value.strip())

        loaded[key] = value
        if override or key not in os.environ:
            os.environ[key] = value

    return loaded
